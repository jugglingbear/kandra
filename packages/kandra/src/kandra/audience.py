"""Audience profiles: repo-level IP-isolation policy + per-file resolution.

See kandra.md section 8.2. Two layers cooperate to decide which source files
may appear in a generated SDK:

1. ``audience_profiles.yaml`` — the repo-level policy. Declares the build
   *profiles* (each with an ``include_audience`` set and a ``deny_substrings``
   leakage denylist) and a **static** path -> audience-tags map. It sits next
   to the manifest so InfoSec has a single auditable file.
2. Per-file ``# kandra-audience:`` header — an in-file override that may
   *narrow* (never *widen*) the YAML grant for a single file.

The default audience for any file not listed in the YAML map is ``internal`` —
authors must consciously opt a file into a wider audience.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from kandra.manifest.model import AudienceTag

if TYPE_CHECKING:
    from collections.abc import Iterable

_yaml = YAML(typ="safe", pure=True)

# Default filename for the repo-level policy, resolved next to the manifest.
AUDIENCE_PROFILES_FILENAME = "audience_profiles.yaml"

# A file not listed in the profiles map defaults to internal-only.
DEFAULT_AUDIENCE: tuple[str, ...] = ("internal",)

# Glob metacharacters are rejected in the static path map: a rename must make an
# entry stop matching (falling to default-internal), never silently re-match.
_GLOB_CHARS = frozenset("*?[]")

# `# kandra-audience: tag1, tag2` — matched only on comment lines.
_AUDIENCE_HEADER_RE = re.compile(r"^\s*#\s*kandra-audience\s*:\s*(?P<tags>.+?)\s*$")

# Reuse the manifest's audience-tag shape so headers and YAML agree.
_AUDIENCE_TAG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class AudienceError(Exception):
    """Raised when an audience profile fails to load or a file cannot resolve.

    The message targets the SDK author: it names the offending file / profile
    and explains the rule that was violated.
    """


class _ProfileModel(BaseModel):
    """Shared config for the audience-profile models."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AudienceProfile(_ProfileModel):
    """One build target: which audiences to include and what must never leak."""

    description: str = ""
    include_audience: list[AudienceTag] = Field(min_length=1)
    deny_substrings: list[str] = Field(default_factory=list)


class AudienceProfiles(_ProfileModel):
    """Repo-level audience policy loaded from ``audience_profiles.yaml``.

    ``files`` is a static path -> audience-tags map. Keys are paths relative to
    the directory holding the profiles file (the same base the manifest's
    ``source_roots`` resolve against). Files absent from the map default to
    :data:`DEFAULT_AUDIENCE`.
    """

    profiles: dict[str, AudienceProfile] = Field(min_length=1)
    files: dict[str, list[AudienceTag]] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def _check_static_paths(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for raw_path, tags in value.items():
            if _GLOB_CHARS.intersection(raw_path):
                raise ValueError(
                    f"path {raw_path!r} contains a glob metacharacter; the audience map is "
                    "static-paths-only (list each file explicitly so a rename fails loud)"
                )
            if not tags:
                raise ValueError(f"path {raw_path!r} maps to an empty audience list")
        return value

    def profile(self, name: str) -> AudienceProfile:
        """Return the named profile or raise :class:`AudienceError`.

        Args:
            name: Profile key to look up.

        Returns:
            The matching :class:`AudienceProfile`.

        Raises:
            AudienceError: No profile is named ``name``.
        """
        try:
            return self.profiles[name]
        except KeyError:
            known = ", ".join(sorted(self.profiles)) or "<none>"
            raise AudienceError(f"unknown profile {name!r}; known profiles: {known}") from None

    def grant_for(self, rel_path: str) -> frozenset[str]:
        """Return the YAML-declared audience grant for ``rel_path``.

        Args:
            rel_path: POSIX path relative to the profiles-file directory.

        Returns:
            The declared tag set, or :data:`DEFAULT_AUDIENCE` when the path is
            not listed.
        """
        listed = self.files.get(rel_path)
        return frozenset(listed) if listed is not None else frozenset(DEFAULT_AUDIENCE)


def load_audience_profiles(source: str | Path) -> AudienceProfiles:
    """Load and validate an audience-profiles config from a path or YAML string.

    A :class:`~pathlib.Path` is read from disk; any other ``str`` is parsed as
    YAML text directly.

    Args:
        source: Filesystem path to ``audience_profiles.yaml`` or raw YAML text.

    Returns:
        The validated :class:`AudienceProfiles`.

    Raises:
        AudienceError: The file cannot be read, is not valid YAML, or fails
            model validation.
    """
    if isinstance(source, Path):
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise AudienceError(f"cannot read audience profiles {source}: {exc}") from exc
        origin = str(source)
    else:
        text = source
        origin = "<string>"

    try:
        raw = _yaml.load(text)
    except YAMLError as exc:
        raise AudienceError(f"{origin}: YAML parse error: {exc}") from exc

    if raw is None:
        raise AudienceError(f"{origin}: audience profiles file is empty")
    if not isinstance(raw, dict):
        raise AudienceError(f"{origin}: top-level audience profiles must be a mapping")

    try:
        return AudienceProfiles.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors())
        raise AudienceError(f"{origin}: invalid audience profiles: {details}") from exc


def parse_audience_header(source_text: str) -> list[str] | None:
    """Extract the ``# kandra-audience:`` tag list from Python source, if present.

    Only comment lines are matched, so the token appearing inside a docstring or
    string literal does not count. The first matching comment wins.

    Args:
        source_text: The full text of a Python source file.

    Returns:
        The parsed tag list (comma/whitespace separated), or ``None`` when the
        file declares no header.
    """
    for line in source_text.splitlines():
        match = _AUDIENCE_HEADER_RE.match(line)
        if match is not None:
            return [tag for tag in re.split(r"[,\s]+", match.group("tags")) if tag]
    return None


def resolve_file_audience(rel_path: str, source_text: str, profiles: AudienceProfiles) -> frozenset[str]:
    """Compute the effective audience of one file: YAML grant narrowed by its header.

    Resolution rules (kandra.md section 8.2):

    ======================  ===============================  ======================
    YAML grant              Header                           Effective audience
    ======================  ===============================  ======================
    (file not listed)       (absent)                         ``internal`` (default)
    ``[a, b, c]``           (absent)                         ``[a, b, c]``
    ``[a, b, c]``           ``# kandra-audience: a``          ``[a]`` (narrowed)
    ``[a]``                 ``# kandra-audience: b``          **error** (cannot widen)
    ======================  ===============================  ======================

    Args:
        rel_path: POSIX path of the file relative to the profiles-file directory.
        source_text: The file's full source text (scanned for the header).
        profiles: The loaded audience policy supplying the YAML grant.

    Returns:
        The effective audience tag set for the file.

    Raises:
        AudienceError: The file's header names a tag outside its YAML grant
            (a forbidden widening) or a malformed tag.
    """
    grant = profiles.grant_for(rel_path)
    header = parse_audience_header(source_text)
    if header is None:
        return grant

    malformed = [tag for tag in header if not _AUDIENCE_TAG_RE.match(tag)]
    if malformed:
        raise AudienceError(f"{rel_path}: malformed audience tag(s) {malformed} in '# kandra-audience:' header")

    header_set = frozenset(header)
    widened = header_set - grant
    if widened:
        raise AudienceError(
            f"{rel_path}: '# kandra-audience:' header names {sorted(widened)} which the "
            f"profiles grant {sorted(grant)} does not include — a header may narrow but never "
            "widen the YAML grant"
        )
    return header_set


def audience_intersects(item_audience: Iterable[str], include_audience: Iterable[str]) -> bool:
    """Return ``True`` when the two audience sets share at least one tag.

    Args:
        item_audience: The audience tags on a manifest entry or resolved file.
        include_audience: The profile's ``include_audience`` set.

    Returns:
        Whether the entry survives pruning for this profile.
    """
    return bool(set(item_audience) & set(include_audience))


def posix_relpath(path: Path, base: Path) -> str:
    """Return ``path`` relative to ``base`` as a POSIX string (forward slashes).

    Used to key files against the audience map consistently across platforms.

    Args:
        path: The file path to relativize.
        base: The directory the audience map's keys are relative to.

    Returns:
        The relative path rendered with ``/`` separators.
    """
    return PurePosixPath(path.resolve().relative_to(base.resolve())).as_posix()
