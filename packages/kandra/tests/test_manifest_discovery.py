"""Tests for the manifest `discovery:` block + scanners.py codegen."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from kandra import LoaderError, load_manifest
from kandra.generator.render import (
    BleDiscoverySpec,
    DiscoverySpec,
    HttpDiscoverySpec,
    render_scanners,
)
from kandra_runtime import Candidate

# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


_BASE_MANIFEST = """
schema_version: 1
device:
  id: pbp
  display_name: PBP
  audience: [internal]
source_roots: [src]
transports:
  - id: ble
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
    family: ble
    channels:
      command: {{ write: "b5f90001-aa8d-11e3-9046-0002a5d5c51b", notify: "b5f90002-aa8d-11e3-9046-0002a5d5c51b" }}
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
    family: http
{discovery}
commands:
  - id: x.go
    handler: pkg.mod:Handler
    transports: [ble, http]
    audience: [internal]
    http:
      http: {{ method: POST, path: /x }}
    ble:
      ble: {{ channel: command }}
"""


def _manifest_with(discovery_yaml: str) -> str:
    return _BASE_MANIFEST.format(discovery=discovery_yaml)


def test_discovery_block_loads_with_ble_only() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery:
              ble:
                name_prefix: "PBP-"
                service_uuids:
                  - "b5f90001-aa8d-11e3-9046-0002a5d5c51b"
                manufacturer_id: 1452
            """
        )
    )
    manifest = load_manifest(yaml)
    assert manifest.discovery is not None
    assert manifest.discovery.ble is not None
    assert manifest.discovery.ble.name_prefix == "PBP-"
    assert manifest.discovery.ble.manufacturer_id == 1452
    assert manifest.discovery.http is None


def test_discovery_block_loads_with_http_only() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery:
              http:
                base_urls: ["http://10.0.0.1", "http://10.0.0.2:8080"]
                probe_path: "/v1/ping"
                server_header_prefix: "PBP"
            """
        )
    )
    manifest = load_manifest(yaml)
    assert manifest.discovery is not None
    assert manifest.discovery.http is not None
    assert manifest.discovery.http.base_urls == ["http://10.0.0.1", "http://10.0.0.2:8080"]
    assert manifest.discovery.http.probe_path == "/v1/ping"


def test_discovery_block_loads_with_both_families() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery:
              ble:
                name_prefix: "PBP-"
              http:
                base_urls: ["http://10.0.0.1"]
            """
        )
    )
    manifest = load_manifest(yaml)
    assert manifest.discovery is not None
    assert manifest.discovery.ble is not None
    assert manifest.discovery.http is not None


def test_discovery_block_is_optional() -> None:
    yaml = _manifest_with("")
    manifest = load_manifest(yaml)
    assert manifest.discovery is None


def test_empty_discovery_block_rejected() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery: {}
            """
        )
    )
    with pytest.raises(LoaderError, match="at least one transport family"):
        load_manifest(yaml)


def test_http_discovery_requires_base_urls() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery:
              http:
                probe_path: "/"
            """
        )
    )
    with pytest.raises(LoaderError):
        load_manifest(yaml)


def test_invalid_uuid_rejected() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery:
              ble:
                service_uuids: ["not-a-uuid"]
            """
        )
    )
    with pytest.raises(LoaderError, match="UUID"):
        load_manifest(yaml)


def test_invalid_base_url_rejected() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery:
              http:
                base_urls: ["10.0.0.1"]
            """
        )
    )
    with pytest.raises(LoaderError, match="http://"):
        load_manifest(yaml)


def test_manufacturer_id_range_enforced() -> None:
    yaml = _manifest_with(
        dedent(
            """\
            discovery:
              ble:
                manufacturer_id: 65536
            """
        )
    )
    with pytest.raises(LoaderError):
        load_manifest(yaml)


def test_discovery_ble_without_ble_transport_rejected() -> None:
    # Build a manifest with only http transport but discovery.ble declared.
    yaml = """
schema_version: 1
device:
  id: pbp
  display_name: PBP
  audience: [internal]
source_roots: [src]
transports:
  - id: http
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
    family: http
discovery:
  ble:
    name_prefix: "PBP-"
commands:
  - id: x.go
    handler: pkg.mod:Handler
    transports: [http]
    audience: [internal]
    http:
      http: { method: POST, path: /x }
"""
    with pytest.raises(LoaderError, match="family='ble'"):
        load_manifest(yaml)


def test_discovery_http_without_http_transport_rejected() -> None:
    yaml = """
schema_version: 1
device:
  id: pbp
  display_name: PBP
  audience: [internal]
source_roots: [src]
transports:
  - id: ble
    adapter: pkg.mod:Adapter
    codec: pkg.mod:Codec
    family: ble
    channels:
      command: { write: "b5f90001-aa8d-11e3-9046-0002a5d5c51b", notify: "b5f90002-aa8d-11e3-9046-0002a5d5c51b" }
discovery:
  http:
    base_urls: ["http://10.0.0.1"]
commands:
  - id: x.go
    handler: pkg.mod:Handler
    transports: [ble]
    audience: [internal]
    ble:
      ble: { channel: command }
"""
    with pytest.raises(LoaderError, match="family='http'"):
        load_manifest(yaml)


# ---------------------------------------------------------------------------
# render_scanners output
# ---------------------------------------------------------------------------


def _load_generated(tmp_path: Path, source: str) -> Any:
    """Write `source` as a temp module and import it under a unique name."""
    mod_name = f"_kandra_test_scanners_{abs(hash(source))}"
    path = tmp_path / f"{mod_name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise
    return module


def test_render_scanners_ble_only_default_matcher(tmp_path: Path) -> None:
    discovery = DiscoverySpec(
        ble=BleDiscoverySpec(
            name_prefix="PBP-",
            service_uuids=("b5f90001-aa8d-11e3-9046-0002a5d5c51b",),
            manufacturer_id=0x05AC,
        ),
        http=None,
    )
    src = render_scanners(discovery)
    mod = _load_generated(tmp_path, src)

    # Matcher accepts a candidate satisfying every criterion.
    good = Candidate(
        transport="ble",
        address="AA:BB:CC:DD:EE:FF",
        advertised_name="PBP-001",
        metadata={
            "service_uuids": ("b5f90001-aa8d-11e3-9046-0002a5d5c51b",),
            "manufacturer_data": {0x05AC: b"\x00"},
        },
    )
    assert mod.default_ble_matcher(good) is True

    # Wrong transport -> reject.
    bad_transport = Candidate(transport="http", address="http://x")
    assert mod.default_ble_matcher(bad_transport) is False

    # Wrong name prefix -> reject.
    bad_name = Candidate(transport="ble", address="AA", advertised_name="OTHER")
    assert mod.default_ble_matcher(bad_name) is False

    # Missing service UUID overlap -> reject.
    bad_uuid = Candidate(
        transport="ble",
        address="AA",
        advertised_name="PBP-002",
        metadata={"service_uuids": ("other-uuid",), "manufacturer_data": {0x05AC: b""}},
    )
    assert mod.default_ble_matcher(bad_uuid) is False

    # Missing manufacturer id -> reject.
    bad_mfr = Candidate(
        transport="ble",
        address="AA",
        advertised_name="PBP-003",
        metadata={
            "service_uuids": ("b5f90001-aa8d-11e3-9046-0002a5d5c51b",),
            "manufacturer_data": {0x004C: b""},
        },
    )
    assert mod.default_ble_matcher(bad_mfr) is False

    # make_ble_scanner returns the runtime BleScanner type.
    from kandra_runtime import BleScanner

    assert isinstance(mod.make_ble_scanner(), BleScanner)


def test_render_scanners_ble_no_criteria_accepts_any_ble(tmp_path: Path) -> None:
    discovery = DiscoverySpec(
        ble=BleDiscoverySpec(name_prefix=None, service_uuids=(), manufacturer_id=None),
        http=None,
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))
    assert mod.default_ble_matcher(Candidate(transport="ble", address="AA")) is True
    assert mod.default_ble_matcher(Candidate(transport="http", address="x")) is False


def test_render_scanners_http_default_matcher(tmp_path: Path) -> None:
    discovery = DiscoverySpec(
        ble=None,
        http=HttpDiscoverySpec(
            base_urls=("http://10.0.0.1", "http://10.0.0.2:8080"),
            probe_path="/v1/ping",
            server_header_prefix="PBP",
        ),
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))

    good = Candidate(transport="http", address="http://10.0.0.1", advertised_name="PBP-server")
    assert mod.default_http_matcher(good) is True

    bad_header = Candidate(transport="http", address="http://x", advertised_name="other")
    assert mod.default_http_matcher(bad_header) is False

    bad_transport = Candidate(transport="ble", address="AA")
    assert mod.default_http_matcher(bad_transport) is False

    # make_http_scanner preserves the manifest's URLs + probe path.
    from kandra_runtime import HttpScanner

    scanner = mod.make_http_scanner()
    assert isinstance(scanner, HttpScanner)


def test_render_scanners_http_no_header_filter_accepts_all_http(tmp_path: Path) -> None:
    discovery = DiscoverySpec(
        ble=None,
        http=HttpDiscoverySpec(
            base_urls=("http://10.0.0.1",), probe_path="/", server_header_prefix=None
        ),
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))
    assert mod.default_http_matcher(Candidate(transport="http", address="x")) is True


def test_render_scanners_both_families_emit_both_helpers(tmp_path: Path) -> None:
    discovery = DiscoverySpec(
        ble=BleDiscoverySpec(name_prefix="X-", service_uuids=(), manufacturer_id=None),
        http=HttpDiscoverySpec(
            base_urls=("http://10.0.0.1",), probe_path="/", server_header_prefix=None
        ),
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))
    assert callable(mod.default_ble_matcher)
    assert callable(mod.make_ble_scanner)
    assert callable(mod.scan_ble)
    assert callable(mod.default_http_matcher)
    assert callable(mod.make_http_scanner)
    assert callable(mod.scan_http)


def test_render_scanners_scan_ble_uses_snapshot_scan(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    discovery = DiscoverySpec(
        ble=BleDiscoverySpec(name_prefix=None, service_uuids=(), manufacturer_id=None),
        http=None,
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))

    seen: dict[str, object] = {}

    async def fake_snapshot_scan(scanner, *, matcher, timeout):  # type: ignore[no-untyped-def]
        seen["scanner"] = scanner
        seen["matcher"] = matcher
        seen["timeout"] = timeout
        return [Candidate(transport="ble", address="AA:BB")]

    monkeypatch.setattr(mod, "snapshot_scan", fake_snapshot_scan)
    result = asyncio.run(mod.scan_ble(timeout=2.5))
    assert len(result) == 1
    assert seen["timeout"] == 2.5
    assert seen["matcher"] is mod.default_ble_matcher


def test_render_scanners_http_scan_uses_manifest_urls_by_default(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    """`scan_http()` with no args probes the manifest-declared base_urls."""
    discovery = DiscoverySpec(
        ble=None,
        http=HttpDiscoverySpec(
            base_urls=("http://10.0.0.1", "http://10.0.0.2:8080"),
            probe_path="/v1/ping",
            server_header_prefix=None,
        ),
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))

    seen: dict[str, object] = {}

    async def fake_snapshot_scan(scanner, *, matcher, timeout):  # type: ignore[no-untyped-def]
        seen["candidates"] = list(scanner._candidates)
        seen["timeout"] = timeout
        return []

    monkeypatch.setattr(mod, "snapshot_scan", fake_snapshot_scan)
    asyncio.run(mod.scan_http(timeout=1.0))
    assert seen["candidates"] == ["http://10.0.0.1", "http://10.0.0.2:8080"]
    assert seen["timeout"] == 1.0


def test_render_scanners_http_scan_accepts_base_urls_override(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    """`scan_http(base_urls=...)` replaces the manifest list — needed for localhost dev."""
    discovery = DiscoverySpec(
        ble=None,
        http=HttpDiscoverySpec(
            base_urls=("http://10.0.0.1",),
            probe_path="/",
            server_header_prefix=None,
        ),
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))

    seen: dict[str, object] = {}

    async def fake_snapshot_scan(scanner, *, matcher, timeout):  # type: ignore[no-untyped-def]
        seen["candidates"] = list(scanner._candidates)
        return []

    monkeypatch.setattr(mod, "snapshot_scan", fake_snapshot_scan)
    asyncio.run(mod.scan_http(timeout=1.0, base_urls=["http://localhost:8080"]))
    assert seen["candidates"] == ["http://localhost:8080"]


def test_render_scanners_make_http_scanner_accepts_base_urls_override(
    tmp_path: Path,
) -> None:
    """`make_http_scanner(base_urls=...)` returns a scanner targeted at the override URLs."""
    discovery = DiscoverySpec(
        ble=None,
        http=HttpDiscoverySpec(
            base_urls=("http://10.0.0.1",),
            probe_path="/v1/ping",
            server_header_prefix=None,
        ),
    )
    mod = _load_generated(tmp_path, render_scanners(discovery))
    scanner = mod.make_http_scanner(base_urls=["http://localhost:1234", "http://localhost:5678"])
    assert scanner._candidates == ["http://localhost:1234", "http://localhost:5678"]
