"""End-to-end tests for attribute code generation (read / write / subscribe facades).

Each test writes a self-contained handler package + manifest into ``tmp_path``,
builds the SDK (compile + clean-import gate on by default), and — for the HTTP
cases — round-trips the generated ``client.<ns>.<attr>.read/.write/.subscribe``
facade over a :class:`LoopbackTransport`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from textwrap import dedent

import pytest
from kandra.generator import BuildError, build_sdk
from kandra_runtime import HttpRequest, HttpResponse, LoopbackTransport, open_transport

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_HANDLER_SRC = dedent(
    '''
    """Attribute handler package for the generator tests."""

    from __future__ import annotations

    from dataclasses import dataclass


    @dataclass(frozen=True)
    class Level:
        """A settable level value."""

        level: int


    @dataclass(frozen=True)
    class WriteAck:
        """A distinct write-acknowledgement payload."""

        ok: bool


    class LevelSetting:
        """`settings.level` handler. Writes echo the value (no separate ack)."""

        value = Level


    class LevelSettingWithAck:
        """`settings.level` handler with a distinct ``write_ack`` type."""

        value = Level
        write_ack = WriteAck


    class NoValueHandler:
        """Malformed handler: missing the required ``value`` attribute."""


    class BadValueHandler:
        """Malformed handler: ``value`` is not a class."""

        value = 42


    class StubBleCodec:
        """Minimal payload codec so the BLE registry entry constructs on import."""

        def __init__(self, request_type: type, response_type: type) -> None:
            self._request_type = request_type
            self._response_type = response_type

        def encode(self, request: object) -> bytes:
            return b""

        def decode(self, payload: bytes) -> object:
            return self._response_type()
    '''
).lstrip()


def _write_sources(tmp_path: Path, slug: str, manifest_body: str) -> Path:
    """Create ``src/<slug>_pkg/attr.py`` + ``manifest.yaml``; return the manifest path."""
    pkg = f"{slug}_pkg"
    src = tmp_path / slug / "src" / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "attr.py").write_text(_HANDLER_SRC, encoding="utf-8")
    manifest = tmp_path / slug / "manifest.yaml"
    resolved = manifest_body.replace("__PKG__", pkg).replace("__SLUG__", slug)
    manifest.write_text(resolved, encoding="utf-8")
    return manifest


@contextmanager
def _built(tmp_path: Path, slug: str, manifest_body: str, *, typecheck: bool = False) -> Iterator[Path]:
    """Build an attribute SDK and expose it (and its src tree) on ``sys.path``."""
    manifest = _write_sources(tmp_path, slug, manifest_body)
    out = tmp_path / slug / "out"
    snapshot = set(sys.modules)
    result = build_sdk(manifest, output_root=out, typecheck=typecheck)
    added = [str(tmp_path / slug / "src"), str(out)]
    sys.path[:0] = added
    try:
        yield result.package_path
    finally:
        for p in added:
            with suppress(ValueError):
                sys.path.remove(p)
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]


_HTTP_ATTR_MANIFEST = dedent(
    """
    schema_version: 1
    device:
      id: __SLUG__
      display_name: Probe
      audience: [internal]
    source_roots: [src]
    transports:
      - id: http
        codec: __PKG__.attr:LevelSetting
        family: http
    attributes:
      - id: settings.level
        handler: __PKG__.attr:LevelSetting
        transports: [http]
        operations: [read, write, subscribe]
        audience: [internal]
        http:
          http:
            read: { method: GET, path: /v1/settings/level }
            write: { method: PUT, path: /v1/settings/level, body_codec: json }
            subscribe: { mode: sse, path: /v1/settings/level/events }
    """
).lstrip()


def _echo_http_handler(req: HttpRequest) -> HttpResponse:
    """PUT echoes its body; GET returns a fixed level."""
    if req.method == "PUT":
        body = json.loads((req.body or b"{}").decode("utf-8"))
        return HttpResponse(status=200, body=json.dumps(body).encode("utf-8"))
    return HttpResponse(status=200, body=json.dumps({"level": 7}).encode("utf-8"))


def _script_sse_handler(levels: tuple[int, ...]) -> object:
    """Build a subscribe handler that pushes one JSON event per level."""

    async def _stream(req: HttpRequest) -> AsyncIterator[HttpResponse]:
        for lvl in levels:
            yield HttpResponse(status=200, body=json.dumps({"level": lvl}).encode("utf-8"))

    return _stream


# ---------------------------------------------------------------------------
# Shape / build-output tests
# ---------------------------------------------------------------------------


def test_attribute_client_and_registry_shapes(tmp_path: Path) -> None:
    """The facade exposes read/write/subscribe; the registry gets one op each."""
    with _built(tmp_path, "shapes", _HTTP_ATTR_MANIFEST) as pkg:
        client_src = (pkg / "client.py").read_text(encoding="utf-8")
        registry_src = (pkg / "registry.py").read_text(encoding="utf-8")

    assert "class _SettingsLevelAttribute:" in client_src
    assert "class _SyncSettingsLevelAttribute:" in client_src
    assert "self.level = _SettingsLevelAttribute(client)" in client_src
    assert "async def read(" in client_src
    assert "async def write(" in client_src
    assert "def subscribe(" in client_src
    assert "_SUBSCRIBE_MODES" in client_src
    assert '"settings.level.subscribe":' in client_src
    for op in ("read", "write", "subscribe"):
        assert f'"settings.level.{op}":' in registry_src


def test_attribute_sdk_passes_mypy_strict(tmp_path: Path) -> None:
    """The generated attribute SDK type-checks under mypy --strict (opt-in gate)."""
    with _built(tmp_path, "typed", _HTTP_ATTR_MANIFEST, typecheck=True) as pkg:
        assert (pkg / "client.py").is_file()


_MIXED_ATTR_MANIFEST = dedent(
    """
    schema_version: 1
    device:
      id: __SLUG__
      display_name: Probe
      audience: [internal]
    source_roots: [src]
    transports:
      - id: http
        codec: __PKG__.attr:LevelSetting
        family: http
    attributes:
      - id: settings.level
        handler: __PKG__.attr:LevelSetting
        transports: [http]
        operations: [read, write, subscribe]
        audience: [internal]
        http:
          http:
            read: { method: GET, path: /v1/settings/level }
            write: { method: PUT, path: /v1/settings/level }
            subscribe: { mode: sse, path: /v1/settings/level/events }
      - id: status.uptime
        handler: __PKG__.attr:LevelSetting
        transports: [http]
        operations: [read]
        audience: [internal]
        http:
          http:
            read: { method: GET, path: /v1/status/uptime }
      - id: alerts.tick
        handler: __PKG__.attr:LevelSetting
        transports: [http]
        operations: [subscribe]
        audience: [internal]
        http:
          http:
            subscribe: { mode: sse, path: /v1/alerts/tick/events }
    """
).lstrip()


def test_attribute_mixed_operations_shapes(tmp_path: Path) -> None:
    """Read-only, subscribe-only, and full attributes coexist and render correctly."""
    with _built(tmp_path, "mixed", _MIXED_ATTR_MANIFEST) as pkg:
        client_src = (pkg / "client.py").read_text(encoding="utf-8")

    # Read-only attribute: no subscribe entry; sync + async facades present.
    assert "class _StatusUptimeAttribute:" in client_src
    assert '"status.uptime.subscribe"' not in client_src
    # Subscribe-only attribute: async subscribe, but the sync facade is a bare stub.
    assert "class _AlertsTickAttribute:" in client_src
    assert '"alerts.tick.subscribe":' in client_src
    sync_tick = client_src.split("class _SyncAlertsTickAttribute:", 1)[1]
    assert "def read(" not in sync_tick.split("class ", 1)[0]
    assert "def write(" not in sync_tick.split("class ", 1)[0]



# ---------------------------------------------------------------------------
# Async dispatch tests
# ---------------------------------------------------------------------------


async def test_attribute_read_and_write_async(tmp_path: Path) -> None:
    """Async read returns the device value; write echoes the submitted value."""
    with _built(tmp_path, "rw", _HTTP_ATTR_MANIFEST):
        from rw_pkg.attr import Level
        from rw_sdk import RwClient, TransportId

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_echo_http_handler)
        async with open_transport(transport) as t:
            client = RwClient(transports={TransportId.HTTP: t})
            read = await client.settings.level.read()
            wrote = await client.settings.level.write(Level(9))

    assert read is not None and read.accepted and read.data == Level(7)
    assert read.extra == {"http_status": 200}
    assert wrote is not None and wrote.accepted and wrote.data == Level(9)


async def test_attribute_subscribe_sse_async(tmp_path: Path) -> None:
    """SSE subscribe yields one typed Result per pushed event."""
    with _built(tmp_path, "sse", _HTTP_ATTR_MANIFEST):
        from sse_pkg.attr import Level
        from sse_sdk import SseClient, TransportId

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(
            _echo_http_handler, subscribe_handler=_script_sse_handler((1, 2, 3))
        )
        async with open_transport(transport) as t:
            client = SseClient(transports={TransportId.HTTP: t})
            seen = [r.data async for r in client.settings.level.subscribe()]

    assert seen == [Level(1), Level(2), Level(3)]


async def test_attribute_subscribe_poll_async(tmp_path: Path) -> None:
    """Poll-mode subscribe repeatedly dispatches the op on an interval."""
    poll_manifest = _HTTP_ATTR_MANIFEST.replace(
        "subscribe: { mode: sse, path: /v1/settings/level/events }",
        "subscribe: { mode: poll, path: /v1/settings/level, interval: 0.01 }",
    )
    with _built(tmp_path, "poll", poll_manifest) as pkg:
        assert 'TransportId.HTTP: ("poll", 0.01)' in (pkg / "client.py").read_text(encoding="utf-8")
        from poll_pkg.attr import Level
        from poll_sdk import PollClient, TransportId

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_echo_http_handler)
        async with open_transport(transport) as t:
            client = PollClient(transports={TransportId.HTTP: t})
            got = []
            async for result in client.settings.level.subscribe():
                got.append(result.data)
                if len(got) >= 3:
                    break

    assert got == [Level(7), Level(7), Level(7)]


def test_attribute_sync_read_and_write(tmp_path: Path) -> None:
    """The sync facade wraps read/write via ``asyncio.run``."""
    with _built(tmp_path, "syncrw", _HTTP_ATTR_MANIFEST):
        from syncrw_pkg.attr import Level
        from syncrw_sdk import SyncSyncrwClient, TransportId

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_echo_http_handler)
        asyncio.run(transport.open())
        try:
            client = SyncSyncrwClient(transports={TransportId.HTTP: transport})
            read = client.settings.level.read()
            wrote = client.settings.level.write(Level(4))
        finally:
            asyncio.run(transport.close())

    assert read is not None and read.accepted and read.data == Level(7)
    assert wrote is not None and wrote.accepted and wrote.data == Level(4)


async def test_attribute_distinct_write_ack(tmp_path: Path) -> None:
    """A handler with a separate ``write_ack`` type decodes writes into that type."""
    manifest = _HTTP_ATTR_MANIFEST.replace(":LevelSetting", ":LevelSettingWithAck")
    with _built(tmp_path, "ack", manifest) as pkg:
        assert "_Ack_settings_level" in (pkg / "registry.py").read_text(encoding="utf-8")
        from ack_pkg.attr import Level, WriteAck
        from ack_sdk import AckClient, TransportId

        def _ack_handler(req: HttpRequest) -> HttpResponse:
            if req.method == "PUT":
                return HttpResponse(status=200, body=json.dumps({"ok": True}).encode("utf-8"))
            return HttpResponse(status=200, body=json.dumps({"level": 7}).encode("utf-8"))

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_ack_handler)
        async with open_transport(transport) as t:
            client = AckClient(transports={TransportId.HTTP: t})
            wrote = await client.settings.level.write(Level(9))

    assert wrote is not None and wrote.accepted and wrote.data == WriteAck(ok=True)


# ---------------------------------------------------------------------------
# BLE wiring (build + shape + via-selection; no live BLE transport)
# ---------------------------------------------------------------------------

_BLE_ATTR_MANIFEST = dedent(
    """
    schema_version: 1
    device:
      id: __SLUG__
      display_name: Probe
      audience: [internal]
    source_roots: [src]
    transports:
      - id: http
        codec: __PKG__.attr:LevelSetting
        family: http
      - id: ble
        codec: __PKG__.attr:StubBleCodec
        family: ble
        channels:
          state: { write: "b5f90001-aa8d-11e3-9046-0002a5d5c51b", notify: "b5f90002-aa8d-11e3-9046-0002a5d5c51b" }
    attributes:
      - id: settings.level
        handler: __PKG__.attr:LevelSetting
        transports: [http, ble]
        operations: [read, write, subscribe]
        audience: [internal]
        http:
          http:
            read: { method: GET, path: /v1/settings/level }
            write: { method: PUT, path: /v1/settings/level }
            subscribe: { mode: sse, path: /v1/settings/level/events }
        ble:
          ble:
            channel: state
    """
).lstrip()


def test_attribute_ble_wiring_builds_and_maps_modes(tmp_path: Path) -> None:
    """An HTTP+BLE attribute builds; subscribe maps HTTP->sse and BLE->ble."""
    with _built(tmp_path, "blewire", _BLE_ATTR_MANIFEST) as pkg:
        client_src = (pkg / "client.py").read_text(encoding="utf-8")
        registry_src = (pkg / "registry.py").read_text(encoding="utf-8")

    assert 'TransportId.HTTP: ("sse", None)' in client_src
    assert 'TransportId.BLE: ("ble", None)' in client_src
    assert "BleChannelCodec(" in registry_src
    assert 'channel="state"' in registry_src


async def test_attribute_via_unwired_transport_raises(tmp_path: Path) -> None:
    """Selecting a declared-but-unwired transport raises a clear ValueError."""
    with _built(tmp_path, "viaerr", _BLE_ATTR_MANIFEST):
        from viaerr_sdk import TransportId, ViaerrClient

        # The op supports BLE, but only HTTP is wired into this client.
        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_echo_http_handler)
        async with open_transport(transport) as t:
            client = ViaerrClient(transports={TransportId.HTTP: t})
            with pytest.raises(ValueError, match="not wired into this client"):
                await client.settings.level.read(via=TransportId.BLE)


# ---------------------------------------------------------------------------
# Build-error tests
# ---------------------------------------------------------------------------


def test_attribute_handler_missing_value_is_refused(tmp_path: Path) -> None:
    """A handler without ``value`` fails the build with a clear message."""
    manifest = _HTTP_ATTR_MANIFEST.replace("handler: __PKG__.attr:LevelSetting", "handler: __PKG__.attr:NoValueHandler")
    manifest_path = _write_sources(tmp_path, "noval", manifest)
    with pytest.raises(BuildError, match="missing required attribute 'value'"):
        build_sdk(manifest_path, output_root=tmp_path / "noval" / "out")


def test_attribute_null_handler_is_refused(tmp_path: Path) -> None:
    """An attribute with no handler cannot generate a facade."""
    manifest = _HTTP_ATTR_MANIFEST.replace("handler: __PKG__.attr:LevelSetting", "handler: null")
    manifest_path = _write_sources(tmp_path, "nullh", manifest)
    with pytest.raises(BuildError, match="a handler is required"):
        build_sdk(manifest_path, output_root=tmp_path / "nullh" / "out")


def test_attribute_id_must_be_dotted(tmp_path: Path) -> None:
    """A single-segment attribute id has no namespace and is refused."""
    manifest = _HTTP_ATTR_MANIFEST.replace("id: settings.level", "id: level")
    manifest_path = _write_sources(tmp_path, "dotless", manifest)
    with pytest.raises(BuildError, match="must be dotted"):
        build_sdk(manifest_path, output_root=tmp_path / "dotless" / "out")


def test_attribute_non_class_value_is_refused(tmp_path: Path) -> None:
    """A handler whose ``value`` is not a class fails the build."""
    manifest = _HTTP_ATTR_MANIFEST.replace(
        "handler: __PKG__.attr:LevelSetting", "handler: __PKG__.attr:BadValueHandler"
    )
    manifest_path = _write_sources(tmp_path, "badval", manifest)
    with pytest.raises(BuildError, match="handler.value must be a class"):
        build_sdk(manifest_path, output_root=tmp_path / "badval" / "out")


# ---------------------------------------------------------------------------
# Profile pipeline (audience prune -> vendor -> leakage scan) with attributes
# ---------------------------------------------------------------------------


def test_attribute_profile_build_vendors_handler(tmp_path: Path) -> None:
    """A profile build prunes by audience, vendors the attribute handler, rewrites imports."""
    manifest_path = _write_sources(tmp_path, "prof", _HTTP_ATTR_MANIFEST)
    profiles = tmp_path / "prof" / "audience_profiles.yaml"
    profiles.write_text(
        "profiles:\n  internal:\n    include_audience: [internal]\n",
        encoding="utf-8",
    )
    result = build_sdk(
        manifest_path,
        output_root=tmp_path / "prof" / "out",
        profile="internal",
        profiles_path=profiles,
        clean=True,
    )

    registry_src = (result.package_path / "registry.py").read_text(encoding="utf-8")
    client_src = (result.package_path / "client.py").read_text(encoding="utf-8")
    internal_dir = result.package_path / "_internal"

    # The value type import is rewritten into the vendored namespace...
    assert "._internal." in registry_src
    assert "._internal." in client_src
    # ...and the handler module is actually vendored under _internal/.
    assert internal_dir.is_dir()
    assert any(p.name == "attr.py" for p in internal_dir.rglob("*.py"))

