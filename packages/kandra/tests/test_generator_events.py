"""End-to-end tests for event code generation (subscribe-only, async-only facades).

Each test writes a self-contained handler package + manifest into ``tmp_path``,
builds the SDK (compile + clean-import gate on by default), and — for the HTTP
cases — round-trips the generated ``client.<ns>.<event>.subscribe()`` facade over
a :class:`LoopbackTransport`.
"""

from __future__ import annotations

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
    """Event (+ command) handler package for the generator tests."""

    from __future__ import annotations

    from dataclasses import dataclass


    @dataclass(frozen=True)
    class Blip:
        """One event emission payload."""

        seq: int


    class BlipEvent:
        """`sensor.blip` handler. ``payload`` is the streamed emission type."""

        payload = Blip


    class NoPayloadHandler:
        """Malformed event handler: missing the required ``payload`` attribute."""


    class BadPayloadHandler:
        """Malformed event handler: ``payload`` is not a class."""

        payload = 42


    @dataclass(frozen=True)
    class PingReq:
        n: int


    @dataclass(frozen=True)
    class PingResp:
        n: int


    class Ping:
        """A plain command handler, to exercise command+event coexistence."""

        request = PingReq
        response = PingResp


    class StubBleCodec:
        """Minimal payload codec so a BLE registry entry constructs on import."""

        def __init__(self, request_type: type, response_type: type) -> None:
            self._response_type = response_type

        def encode(self, request: object) -> bytes:
            return b""

        def decode(self, payload: bytes) -> object:
            return self._response_type()
    '''
).lstrip()


def _write_sources(tmp_path: Path, slug: str, manifest_body: str) -> Path:
    """Create ``src/<slug>_pkg/ev.py`` + ``manifest.yaml``; return the manifest path."""
    pkg = f"{slug}_pkg"
    src = tmp_path / slug / "src" / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "ev.py").write_text(_HANDLER_SRC, encoding="utf-8")
    manifest = tmp_path / slug / "manifest.yaml"
    manifest.write_text(manifest_body.replace("__PKG__", pkg).replace("__SLUG__", slug), encoding="utf-8")
    return manifest


@contextmanager
def _built(tmp_path: Path, slug: str, manifest_body: str, *, typecheck: bool = False) -> Iterator[Path]:
    """Build an event SDK and expose it (and its src tree) on ``sys.path``."""
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


_HTTP_EVENT_MANIFEST = dedent(
    """
    schema_version: 1
    device:
      id: __SLUG__
      display_name: Probe
      audience: [internal]
    source_roots: [src]
    transports:
      - id: http
        adapter: __PKG__.ev:Ping
        codec: __PKG__.ev:Ping
        family: http
    events:
      - id: sensor.blip
        handler: __PKG__.ev:BlipEvent
        transports: [http]
        audience: [internal]
        http:
          http: { mode: sse, path: /events/blip }
    """
).lstrip()


def _script_sse_handler(seqs: tuple[int, ...]) -> object:
    """Build a subscribe handler that pushes one JSON event per seq value."""

    async def _stream(req: HttpRequest) -> AsyncIterator[HttpResponse]:
        for seq in seqs:
            yield HttpResponse(status=200, body=json.dumps({"seq": seq}).encode("utf-8"))

    return _stream


# ---------------------------------------------------------------------------
# Shape / build-output tests
# ---------------------------------------------------------------------------


def test_event_client_and_registry_shapes(tmp_path: Path) -> None:
    """The facade exposes a subscribe-only event sub-object; the registry gets one op."""
    with _built(tmp_path, "shapes", _HTTP_EVENT_MANIFEST) as pkg:
        client_src = (pkg / "client.py").read_text(encoding="utf-8")
        registry_src = (pkg / "registry.py").read_text(encoding="utf-8")

    assert "class _SensorBlipEvent:" in client_src
    assert "self.blip = _SensorBlipEvent(client)" in client_src
    assert "def subscribe(" in client_src
    assert "_SUBSCRIBE_MODES" in client_src
    assert '"sensor.blip.subscribe":' in client_src
    assert '"sensor.blip.subscribe":' in registry_src
    # Events are async-only: no sync sub-object, and the event-only namespace is
    # absent from the sync facade entirely.
    assert "_SyncSensorNamespace" not in client_src


def test_event_sdk_passes_mypy_strict(tmp_path: Path) -> None:
    """The generated event SDK type-checks under mypy --strict (opt-in gate)."""
    with _built(tmp_path, "typed", _HTTP_EVENT_MANIFEST, typecheck=True) as pkg:
        assert (pkg / "client.py").is_file()


# ---------------------------------------------------------------------------
# Async dispatch tests
# ---------------------------------------------------------------------------


async def test_event_subscribe_sse_async(tmp_path: Path) -> None:
    """SSE subscribe yields one typed Result per pushed emission."""
    with _built(tmp_path, "sse", _HTTP_EVENT_MANIFEST):
        from sse_pkg.ev import Blip
        from sse_sdk import SseClient, TransportId

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(
            lambda req: HttpResponse(status=200, body=b"{}"),
            subscribe_handler=_script_sse_handler((1, 2, 3)),
        )
        async with open_transport(transport) as t:
            client = SseClient(transports={TransportId.HTTP: t})
            seen = [r.data async for r in client.sensor.blip.subscribe()]

    assert seen == [Blip(1), Blip(2), Blip(3)]


async def test_event_subscribe_poll_async(tmp_path: Path) -> None:
    """Poll-mode event subscribe repeatedly re-reads the endpoint on an interval."""
    poll_manifest = _HTTP_EVENT_MANIFEST.replace(
        "http: { mode: sse, path: /events/blip }",
        "http: { mode: poll, path: /events/blip, interval: 0.01 }",
    )
    with _built(tmp_path, "poll", poll_manifest) as pkg:
        assert 'TransportId.HTTP: ("poll", 0.01)' in (pkg / "client.py").read_text(encoding="utf-8")
        from poll_pkg.ev import Blip
        from poll_sdk import PollClient, TransportId

        def _handler(req: HttpRequest) -> HttpResponse:
            return HttpResponse(status=200, body=json.dumps({"seq": 9}).encode("utf-8"))

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_handler)
        async with open_transport(transport) as t:
            client = PollClient(transports={TransportId.HTTP: t})
            got = []
            async for result in client.sensor.blip.subscribe():
                got.append(result.data)
                if len(got) >= 3:
                    break

    assert got == [Blip(9), Blip(9), Blip(9)]


# ---------------------------------------------------------------------------
# Coexistence + BLE wiring
# ---------------------------------------------------------------------------

_MIXED_MANIFEST = dedent(
    """
    schema_version: 1
    device:
      id: __SLUG__
      display_name: Probe
      audience: [internal]
    source_roots: [src]
    transports:
      - id: http
        adapter: __PKG__.ev:Ping
        codec: __PKG__.ev:Ping
        family: http
      - id: ble
        adapter: __PKG__.ev:StubBleCodec
        codec: __PKG__.ev:StubBleCodec
        family: ble
        channels:
          q: { write: "b5f90001-aa8d-11e3-9046-0002a5d5c51b", notify: "b5f90002-aa8d-11e3-9046-0002a5d5c51b" }
    commands:
      - id: sys.ping
        handler: __PKG__.ev:Ping
        transports: [http]
        audience: [internal]
        http:
          http: { method: POST, path: /ping }
    events:
      - id: sensor.blip
        handler: __PKG__.ev:BlipEvent
        transports: [http, ble]
        audience: [internal]
        http:
          http: { mode: sse, path: /events/blip }
        ble:
          ble: { channel: q }
    """
).lstrip()


def test_event_ble_wiring_and_command_coexistence(tmp_path: Path) -> None:
    """A command + an HTTP/BLE event coexist; subscribe maps HTTP->sse and BLE->ble."""
    with _built(tmp_path, "mixed", _MIXED_MANIFEST) as pkg:
        client_src = (pkg / "client.py").read_text(encoding="utf-8")
        registry_src = (pkg / "registry.py").read_text(encoding="utf-8")

    assert "async def ping(" in client_src  # command still rendered
    assert 'TransportId.HTTP: ("sse", None)' in client_src
    assert 'TransportId.BLE: ("ble", None)' in client_src
    assert "BleChannelCodec(" in registry_src
    assert 'channel="q"' in registry_src


async def test_event_via_unwired_transport_raises(tmp_path: Path) -> None:
    """Selecting a declared-but-unwired transport raises a clear ValueError."""
    with _built(tmp_path, "viaerr", _MIXED_MANIFEST):
        from viaerr_sdk import TransportId, ViaerrClient

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(
            lambda req: HttpResponse(status=200, body=b"{}"),
            subscribe_handler=_script_sse_handler((1,)),
        )
        async with open_transport(transport) as t:
            client = ViaerrClient(transports={TransportId.HTTP: t})
            agen = client.sensor.blip.subscribe(via=TransportId.BLE)
            with pytest.raises(ValueError, match="not wired into this client"):
                await agen.__anext__()


# ---------------------------------------------------------------------------
# Profile pipeline
# ---------------------------------------------------------------------------


def test_event_profile_build_vendors_handler(tmp_path: Path) -> None:
    """A profile build prunes by audience, vendors the event handler, rewrites imports."""
    manifest_path = _write_sources(tmp_path, "prof", _HTTP_EVENT_MANIFEST)
    profiles = tmp_path / "prof" / "audience_profiles.yaml"
    profiles.write_text("profiles:\n  internal:\n    include_audience: [internal]\n", encoding="utf-8")
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

    assert "._internal." in registry_src
    assert "._internal." in client_src
    assert internal_dir.is_dir()
    assert any(p.name == "ev.py" for p in internal_dir.rglob("*.py"))


# ---------------------------------------------------------------------------
# Build-error tests
# ---------------------------------------------------------------------------


def test_event_handler_missing_payload_is_refused(tmp_path: Path) -> None:
    """An event handler without ``payload`` fails the build with a clear message."""
    manifest = _HTTP_EVENT_MANIFEST.replace("handler: __PKG__.ev:BlipEvent", "handler: __PKG__.ev:NoPayloadHandler")
    manifest_path = _write_sources(tmp_path, "nopay", manifest)
    with pytest.raises(BuildError, match="missing required attribute 'payload'"):
        build_sdk(manifest_path, output_root=tmp_path / "nopay" / "out")


def test_event_non_class_payload_is_refused(tmp_path: Path) -> None:
    """An event handler whose ``payload`` is not a class fails the build."""
    manifest = _HTTP_EVENT_MANIFEST.replace("handler: __PKG__.ev:BlipEvent", "handler: __PKG__.ev:BadPayloadHandler")
    manifest_path = _write_sources(tmp_path, "badpay", manifest)
    with pytest.raises(BuildError, match="handler.payload must be a class"):
        build_sdk(manifest_path, output_root=tmp_path / "badpay" / "out")


def test_event_null_handler_is_refused(tmp_path: Path) -> None:
    """An event with no handler cannot generate a facade."""
    manifest = _HTTP_EVENT_MANIFEST.replace("handler: __PKG__.ev:BlipEvent", "handler: null")
    manifest_path = _write_sources(tmp_path, "nullh", manifest)
    with pytest.raises(BuildError, match="a handler is required"):
        build_sdk(manifest_path, output_root=tmp_path / "nullh" / "out")


def test_event_id_must_be_dotted(tmp_path: Path) -> None:
    """A single-segment event id has no namespace and is refused."""
    manifest = _HTTP_EVENT_MANIFEST.replace("id: sensor.blip", "id: blip")
    manifest_path = _write_sources(tmp_path, "dotless", manifest)
    with pytest.raises(BuildError, match="must be dotted"):
        build_sdk(manifest_path, output_root=tmp_path / "dotless" / "out")
