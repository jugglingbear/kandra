"""Round-trip and validation tests for the manifest loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from kandra import LoaderError, Manifest, load_manifest

EXAMPLE_MANIFEST = Path(__file__).resolve().parents[3] / "examples" / "pneumatic_bear_poker" / "manifest.yaml"


def test_example_manifest_loads() -> None:
    manifest = load_manifest(EXAMPLE_MANIFEST)
    assert manifest.device.id == "pneumatic_bear_poker"
    assert {t.id for t in manifest.transports} == {"ble", "http"}
    assert {c.id for c in manifest.commands} == {
        "poker.deploy",
        "safety.emergency_retract",
        "power.on",
        "power.off",
        "logs.download",
    }


def test_round_trip_via_dict() -> None:
    """Load → dump → load should produce an equivalent manifest."""
    original = load_manifest(EXAMPLE_MANIFEST)
    dumped = original.model_dump(mode="python")
    reloaded = Manifest.model_validate(dumped)
    assert reloaded == original


def test_null_handler_is_rejected() -> None:
    """Per current scope: handler=null is reserved but unimplemented."""
    bad = """
schema_version: 1
device:
  id: foo
  display_name: Foo
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
commands:
  - id: x
    handler: null
    transports: [http]
    audience: [internal]
"""
    with pytest.raises(LoaderError, match="synthesized default handlers"):
        load_manifest(bad)


def test_unknown_field_is_rejected() -> None:
    bad = """
schema_version: 1
device:
  id: foo
  display_name: Foo
  audience: [internal]
  bogus_field: yes
source_roots: [src]
transports:
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
commands:
  - id: x
    handler: pkg.mod:Handler
    transports: [http]
    audience: [internal]
"""
    with pytest.raises(LoaderError, match="bogus_field"):
        load_manifest(bad)


def test_command_references_undefined_transport() -> None:
    bad = """
schema_version: 1
device:
  id: foo
  display_name: Foo
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
commands:
  - id: x
    handler: pkg.mod:Handler
    transports: [serial]
    audience: [internal]
"""
    with pytest.raises(LoaderError, match="undefined transport"):
        load_manifest(bad)


def test_duplicate_transport_id_is_rejected() -> None:
    bad = """
schema_version: 1
device:
  id: foo
  display_name: Foo
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
  - id: http
    adapter: pkg.mod:Other
    codec: pkg.mod:Codec
commands:
  - id: x
    handler: pkg.mod:Handler
    transports: [http]
    audience: [internal]
"""
    with pytest.raises(LoaderError, match="duplicate transport ids"):
        load_manifest(bad)


def test_bad_dotted_path_is_rejected() -> None:
    bad = """
schema_version: 1
device:
  id: foo
  display_name: Foo
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: not-a-dotted-path
    codec: pkg.mod:Codec
commands:
  - id: x
    handler: pkg.mod:Handler
    transports: [http]
    audience: [internal]
"""
    with pytest.raises(LoaderError, match="dotted path"):
        load_manifest(bad)


def test_handler_field_required_must_be_present() -> None:
    """Per design: `handler` is required (may be null, but cannot be omitted)."""
    bad = """
schema_version: 1
device:
  id: foo
  display_name: Foo
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
commands:
  - id: x
    transports: [http]
    audience: [internal]
"""
    with pytest.raises(LoaderError, match="handler"):
        load_manifest(bad)


def test_unsupported_schema_version() -> None:
    bad = """
schema_version: 999
device:
  id: foo
  display_name: Foo
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
commands:
  - id: x
    handler: pkg.mod:Handler
    transports: [http]
    audience: [internal]
"""
    with pytest.raises(LoaderError, match="schema_version"):
        load_manifest(bad)


def test_empty_manifest_rejected() -> None:
    with pytest.raises(LoaderError, match="empty"):
        load_manifest("")


def test_json_schema_emits() -> None:
    """The JSON Schema should be serializable and contain the expected sections."""
    import json

    schema = Manifest.model_json_schema()
    payload = json.dumps(schema)
    assert '"Device"' in payload
    assert '"Transport"' in payload
    assert '"Command"' in payload
    # Reserved primitives still appear in the schema (authoring is allowed
    # before the runtime supports them).
    assert '"Attribute"' in payload
    assert '"Event"' in payload
