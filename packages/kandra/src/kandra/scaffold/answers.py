"""Answer model captured by the wizard and consumed by the renderer.

The wizard collects user input (or loads it from a YAML fixture) into an
:class:`Answers` instance. The renderer takes that instance and writes a
template-driven Poetry project to disk.

The model is intentionally permissive about field shapes so the
non-interactive YAML format stays compact and forgiving. Validation
beyond field types is done at render time.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Slug: lowercase letters, digits, hyphens; must start with a letter.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# Python identifier: lowercase letters, digits, underscores; starts with a letter.
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")

TransportFamily = Literal["ble", "http"]
WireFormat = Literal["json", "protobuf", "raw"]
DependencySource = Literal["local", "pypi"]


def _slugify(name: str) -> str:
    """Lowercase, hyphen-separated, alnum-only — safe for dirs and PyPI names."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        slug = "device-sdk"
    if not slug[0].isalpha():
        slug = "x-" + slug
    return slug


def _pythonize(slug: str) -> str:
    """Convert ``my-cool-device`` to ``my_cool_device`` for Python imports."""
    ident = slug.replace("-", "_")
    if not _IDENT_RE.match(ident):
        raise ValueError(f"cannot derive a valid Python identifier from slug {slug!r}")
    return ident


def _deviceize(slug: str) -> str:
    """Derive a device identifier from a slug, dropping a trailing ``sdk`` token.

    The build generator forms the generated client package as
    ``<device_id>_sdk`` (and the client class as ``<DeviceId>Client``). If the
    device_id already ends in ``sdk`` — which happens constantly because SDK
    projects are named ``foo-sdk`` — the package doubles up to ``foo_sdk_sdk``
    and the class becomes ``FooSdkClient``. Stripping the suffix here keeps the
    *device* identity (``foo``) distinct from the *SDK* packaging suffix.
    """
    ident = _pythonize(slug)
    stripped = re.sub(r"_?sdk$", "", ident)
    # If the slug was nothing but ``sdk`` (or stripping left an invalid
    # identifier), keep the original rather than emit an empty/illegal id.
    if not stripped or not _IDENT_RE.match(stripped):
        return ident
    return stripped


class TransportAnswer(BaseModel):
    """Per-transport wizard answer."""

    model_config = ConfigDict(extra="forbid")

    family: TransportFamily
    wire_format: WireFormat = "json"
    # BLE-only:
    service_uuid: str | None = None
    name_prefix: str | None = None
    # HTTP-only:
    base_url: str | None = None

    @model_validator(mode="after")
    def _check_family_fields(self) -> TransportAnswer:
        if self.family == "ble":
            # Service UUID / name_prefix are optional — placeholders go into the
            # generated manifest when missing, with TODO comments.
            pass
        elif self.family == "http":
            # base_url likewise optional; default is filled in by renderer.
            pass
        return self


class Answers(BaseModel):
    """Complete set of wizard answers needed to render a scaffold."""

    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(min_length=1)
    slug: str
    package_name: str
    device_id: str
    display_name: str = Field(min_length=1)
    codename: str | None = None
    audience: list[str] = Field(default_factory=lambda: ["public"], min_length=1)
    transports: list[TransportAnswer] = Field(min_length=1)
    python_version: str = Field(default="3.11")
    # How the generated project depends on kandra / kandra-runtime:
    #   "local" → editable path deps pointing at this checkout (installs now)
    #   "pypi"  → ordinary version constraints (once the packages are released)
    dependency_source: DependencySource = "local"

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                f"invalid slug {value!r}: lowercase letters/digits/hyphens, must start with a letter"
            )
        return value

    @field_validator("package_name", "device_id")
    @classmethod
    def _check_ident(cls, value: str) -> str:
        if not _IDENT_RE.match(value):
            raise ValueError(
                f"invalid Python identifier {value!r}: lowercase letters/digits/underscores, "
                "must start with a letter"
            )
        return value

    @model_validator(mode="after")
    def _check_audience(self) -> Answers:
        for tag in self.audience:
            if not re.match(r"^[a-z][a-z0-9_-]*$", tag):
                raise ValueError(f"invalid audience tag {tag!r}")
        return self

    @classmethod
    def from_partial(
        cls,
        *,
        project_name: str,
        display_name: str | None = None,
        codename: str | None = None,
        transports: list[TransportAnswer],
        audience: list[str] | None = None,
        python_version: str = "3.11",
        slug: str | None = None,
        package_name: str | None = None,
        device_id: str | None = None,
        dependency_source: DependencySource = "local",
    ) -> Answers:
        """Build an :class:`Answers` by deriving slug / package / device_id when omitted."""
        derived_slug = slug or _slugify(project_name)
        derived_pkg = package_name or _pythonize(derived_slug)
        derived_dev = device_id or _deviceize(derived_slug)
        return cls(
            project_name=project_name,
            slug=derived_slug,
            package_name=derived_pkg,
            device_id=derived_dev,
            display_name=display_name or project_name,
            codename=codename,
            audience=audience or ["public"],
            transports=transports,
            python_version=python_version,
            dependency_source=dependency_source,
        )
