"""``kandra audit`` — report the effective audience of every source file per profile.

InfoSec runs this before signing off a partner release. It answers two
questions for a given profile:

* **What ships?** The resolved effective audience (YAML grant narrowed by any
  ``# kandra-audience:`` header) of every ``.py`` file under the manifest's
  source roots, and whether that file is included for the profile.
* **What's stale?** Entries in ``audience_profiles.yaml``'s static ``files``
  map that no longer resolve to a real file — a rename left them dangling, so
  the referenced file has silently fallen back to the default ``internal``
  grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kandra.audience import (
    AUDIENCE_PROFILES_FILENAME,
    load_audience_profiles,
    posix_relpath,
    resolve_file_audience,
)
from kandra.loader import load_manifest


@dataclass(frozen=True)
class FileAudit:
    """The audit result for a single source file.

    Attributes:
        rel_path: POSIX path relative to the manifest directory.
        effective: The resolved effective audience tag set.
        included: Whether the file is included for the audited profile.
    """

    rel_path: str
    effective: frozenset[str]
    included: bool


@dataclass(frozen=True)
class AuditReport:
    """The full audit for one profile.

    Attributes:
        profile: The audited profile name.
        include_audience: The profile's ``include_audience`` set.
        files: Per-file audit rows, sorted by path.
        stale_paths: ``files``-map keys that no longer resolve to a real file.
    """

    profile: str
    include_audience: tuple[str, ...]
    files: tuple[FileAudit, ...]
    stale_paths: tuple[str, ...]


def audit_profile(manifest_path: Path, profile: str, *, profiles_path: Path | None = None) -> AuditReport:
    """Compute the effective-audience report for ``profile``.

    Args:
        manifest_path: Path to the device manifest YAML.
        profile: The audience profile to audit.
        profiles_path: Override for the default
            ``<manifest_dir>/audience_profiles.yaml`` location.

    Returns:
        The :class:`AuditReport`.

    Raises:
        AudienceError: The profiles file is invalid or ``profile`` is unknown.
        LoaderError: The manifest cannot be loaded.
    """
    manifest_path = manifest_path.resolve()
    manifest = load_manifest(manifest_path)
    manifest_dir = manifest_path.parent
    roots = [(manifest_dir / r).resolve() for r in manifest.source_roots]

    profiles_file = profiles_path or (manifest_dir / AUDIENCE_PROFILES_FILENAME)
    profiles = load_audience_profiles(profiles_file)
    selected = profiles.profile(profile)
    include = set(selected.include_audience)

    rows: list[FileAudit] = []
    seen: set[Path] = set()
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rel = posix_relpath(resolved, manifest_dir)
            effective = resolve_file_audience(rel, resolved.read_text(encoding="utf-8"), profiles)
            rows.append(FileAudit(rel_path=rel, effective=effective, included=bool(include & effective)))

    stale = tuple(sorted(key for key in profiles.files if not (manifest_dir / key).exists()))
    return AuditReport(
        profile=profile,
        include_audience=tuple(selected.include_audience),
        files=tuple(sorted(rows, key=lambda r: r.rel_path)),
        stale_paths=stale,
    )


def format_report(report: AuditReport) -> str:
    """Render an :class:`AuditReport` as human-readable text.

    Args:
        report: The report to render.

    Returns:
        A multi-line string: a header, one row per file (``+`` included /
        ``-`` pruned), and a stale-path section when any exist.
    """
    lines = [
        f"audit profile={report.profile} include_audience={sorted(report.include_audience)}",
        "",
    ]
    for row in report.files:
        marker = "+" if row.included else "-"
        lines.append(f"  {marker} {row.rel_path}  [{', '.join(sorted(row.effective))}]")

    if report.stale_paths:
        lines.append("")
        lines.append("stale files-map entries (path no longer exists — defaults to internal):")
        lines.extend(f"  ! {path}" for path in report.stale_paths)
    return "\n".join(lines)
