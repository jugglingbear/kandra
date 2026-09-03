"""YAML → Manifest loader with friendly error messages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from kandra.manifest import Manifest

_yaml = YAML(typ="safe", pure=True)


class LoaderError(Exception):
    """Raised when a manifest cannot be loaded or validated.

    The message is intended for end-user (SDK author) consumption — it
    should make the problem locatable without requiring a stack trace.
    """


def load_manifest(source: str | Path) -> Manifest:
    """Load and validate a manifest from a YAML file path or YAML string.

    A `Path` is read from disk; any other `str` is parsed as YAML directly.
    """
    if isinstance(source, Path):
        text = _read_file(source)
        origin = str(source)
    else:
        text = source
        origin = "<string>"

    raw = _parse_yaml(text, origin)
    return _validate(raw, origin)


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoaderError(f"cannot read manifest {path}: {exc}") from exc


def _parse_yaml(text: str, origin: str) -> dict[str, Any]:
    try:
        data = _yaml.load(text)
    except YAMLError as exc:
        raise LoaderError(f"{origin}: YAML parse error: {exc}") from exc

    if data is None:
        raise LoaderError(f"{origin}: manifest is empty")
    if not isinstance(data, dict):
        raise LoaderError(
            f"{origin}: top-level manifest must be a mapping, got {type(data).__name__}"
        )
    return data


def _validate(raw: dict[str, Any], origin: str) -> Manifest:
    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        raise LoaderError(_format_validation_error(exc, origin)) from exc


def _format_validation_error(exc: ValidationError, origin: str) -> str:
    lines = [f"{origin}: manifest validation failed ({exc.error_count()} error(s)):"]
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {location}: {err['msg']}")
    return "\n".join(lines)
