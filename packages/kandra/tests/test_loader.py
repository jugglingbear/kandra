"""Unit tests for :mod:`kandra.loader` — YAML I/O and parse error paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from kandra import LoaderError, Manifest, load_manifest

_MINIMAL = """
schema_version: 1
device:
  id: d
  display_name: D
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: a.b:C
    codec: a.b:D
    family: http
commands:
  - id: x.y
    handler: a.b:H
    transports: [http]
    audience: [internal]
    http:
      http:
        method: GET
        path: /
"""


def test_load_valid_manifest_from_string() -> None:
    manifest = load_manifest(_MINIMAL)
    assert isinstance(manifest, Manifest)
    assert manifest.device.id == "d"


def test_load_missing_file_raises() -> None:
    with pytest.raises(LoaderError, match="cannot read manifest"):
        load_manifest(Path("/no/such/manifest-xyz.yaml"))


def test_load_invalid_yaml_raises() -> None:
    with pytest.raises(LoaderError, match="YAML parse error"):
        load_manifest("device: [unclosed\n")


def test_load_empty_manifest_raises() -> None:
    with pytest.raises(LoaderError, match="manifest is empty"):
        load_manifest("")


def test_load_non_mapping_top_level_raises() -> None:
    with pytest.raises(LoaderError, match="must be a mapping"):
        load_manifest("- a\n- b\n")


def test_validation_error_is_formatted_with_locations() -> None:
    with pytest.raises(LoaderError, match="validation failed"):
        load_manifest("schema_version: 1\n")  # missing device/transports/etc.
