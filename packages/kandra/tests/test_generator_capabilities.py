"""End-to-end tests for capability negotiation codegen (discover + gating).

Each test writes a self-contained handler package + manifest into ``tmp_path``,
builds the SDK, and exercises ``client.discover_capabilities(probe)`` plus the
resulting ``CapabilityUnavailableError`` gating over a :class:`LoopbackTransport`.
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
from kandra.generator import build_sdk
from kandra_runtime import (
    CapabilityUnavailableError,
    HttpRequest,
    HttpResponse,
    LoopbackTransport,
    StaticCapabilityProbe,
    open_transport,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_HANDLER_SRC = dedent(
    '''
    """Handler package for the capability-gating tests."""

    from __future__ import annotations

    from dataclasses import dataclass, field


    @dataclass(frozen=True)
    class Shot:
        iso: int


    class Shutter:
        request = Shot
        response = Shot


    class NightPhoto:
        request = Shot
        response = Shot


    @dataclass(frozen=True)
    class Hdr:
        on: bool


    class HdrSetting:
        value = Hdr


    @dataclass(frozen=True)
    class Motion:
        zone: int


    class MotionEvent:
        payload = Motion


    @dataclass(frozen=True)
    class InfoReq:
        pass


    @dataclass(frozen=True)
    class InfoResp:
        features: list[str] = field(default_factory=list)


    class GetInfo:
        request = InfoReq
        response = InfoResp
    '''
).lstrip()

_CAP_MANIFEST = dedent(
    """
    schema_version: 1
    device:
      id: __SLUG__
      display_name: Cam
      audience: [internal]
    source_roots: [src]
    transports:
      - id: http
        codec: __PKG__.h:Shutter
        family: http
    commands:
      - id: camera.shutter
        handler: __PKG__.h:Shutter
        transports: [http]
        audience: [internal]
        http:
          http: { method: POST, path: /shutter }
      - id: camera.night_photo
        handler: __PKG__.h:NightPhoto
        transports: [http]
        audience: [internal]
        capabilities: [night]
        http:
          http: { method: POST, path: /night }
      - id: system.info
        handler: __PKG__.h:GetInfo
        transports: [http]
        audience: [internal]
        http:
          http: { method: GET, path: /info }
    attributes:
      - id: settings.hdr
        handler: __PKG__.h:HdrSetting
        transports: [http]
        operations: [read, write]
        audience: [internal]
        capabilities: [hdr]
        http:
          http:
            read: { method: GET, path: /hdr }
            write: { method: PUT, path: /hdr }
    events:
      - id: sensor.motion
        handler: __PKG__.h:MotionEvent
        transports: [http]
        audience: [internal]
        capabilities: [motion]
        http:
          http: { mode: sse, path: /motion }
    """
).lstrip()


def _write_sources(tmp_path: Path, slug: str, manifest_body: str) -> Path:
    pkg = f"{slug}_pkg"
    src = tmp_path / slug / "src" / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "h.py").write_text(_HANDLER_SRC, encoding="utf-8")
    manifest = tmp_path / slug / "manifest.yaml"
    manifest.write_text(manifest_body.replace("__PKG__", pkg).replace("__SLUG__", slug), encoding="utf-8")
    return manifest


@contextmanager
def _built(tmp_path: Path, slug: str, manifest_body: str, *, typecheck: bool = False) -> Iterator[Path]:
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


def _handler(req: HttpRequest) -> HttpResponse:
    if req.method == "PUT":
        return HttpResponse(status=200, body=req.body or b"{}")
    if "info" in req.path:
        return HttpResponse(status=200, body=json.dumps({"features": ["night", "hdr"]}).encode())
    if "hdr" in req.path:
        return HttpResponse(status=200, body=json.dumps({"on": True}).encode())
    return HttpResponse(status=200, body=json.dumps({"iso": 800}).encode())


async def _motion_stream(req: HttpRequest) -> AsyncIterator[HttpResponse]:
    yield HttpResponse(status=200, body=json.dumps({"zone": 1}).encode())


def _loopback() -> LoopbackTransport[HttpRequest, HttpResponse]:
    return LoopbackTransport(_handler, subscribe_handler=_motion_stream)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_capability_table_and_methods_rendered(tmp_path: Path) -> None:
    with _built(tmp_path, "shape", _CAP_MANIFEST) as pkg:
        client_src = (pkg / "client.py").read_text(encoding="utf-8")

    assert "_CAPABILITIES: dict[str, tuple[str, ...]] = {" in client_src
    assert '"camera.night_photo": ("night",),' in client_src
    assert '"settings.hdr.read": ("hdr",),' in client_src
    assert '"settings.hdr.write": ("hdr",),' in client_src
    assert '"sensor.motion.subscribe": ("motion",),' in client_src
    assert "async def discover_capabilities(self, probe: CapabilityProbe)" in client_src
    assert "def _check_capability(self, command_id: str) -> None:" in client_src
    # An ungated command must not appear in the table.
    assert '"camera.shutter":' not in client_src


def test_capability_sdk_passes_mypy_strict(tmp_path: Path) -> None:
    with _built(tmp_path, "typed", _CAP_MANIFEST, typecheck=True) as pkg:
        assert (pkg / "client.py").is_file()


def test_no_capabilities_means_no_machinery(tmp_path: Path) -> None:
    """A manifest that gates nothing produces no capability code at all."""
    plain = dedent(
        """
        schema_version: 1
        device: { id: __SLUG__, display_name: D, audience: [internal] }
        source_roots: [src]
        transports:
          - { id: http, codec: __PKG__.h:Shutter, family: http }
        commands:
          - id: camera.shutter
            handler: __PKG__.h:Shutter
            transports: [http]
            audience: [internal]
            http: { http: { method: POST, path: /s } }
        """
    ).lstrip()
    with _built(tmp_path, "plain", plain) as pkg:
        client_src = (pkg / "client.py").read_text(encoding="utf-8")
    assert "_CAPABILITIES" not in client_src
    assert "discover_capabilities" not in client_src
    assert "CapabilityProbe" not in client_src


# ---------------------------------------------------------------------------
# Gating behaviour
# ---------------------------------------------------------------------------


async def test_ungated_before_discovery(tmp_path: Path) -> None:
    """Before discover_capabilities(), even a gated op dispatches normally."""
    with _built(tmp_path, "predisc", _CAP_MANIFEST):
        from predisc_pkg.h import Shot
        from predisc_sdk import PrediscClient, TransportId

        async with open_transport(_loopback()) as t:
            client = PrediscClient(transports={TransportId.HTTP: t})
            result = await client.camera.night_photo(Shot(iso=100))

    assert result is not None and result.accepted


async def test_gating_across_command_attribute_event(tmp_path: Path) -> None:
    """After discovery, supported ops run and unsupported ops raise locally."""
    with _built(tmp_path, "gate", _CAP_MANIFEST):
        from gate_pkg.h import Hdr, Shot
        from gate_sdk import GateClient, TransportId

        async with open_transport(_loopback()) as t:
            client = GateClient(transports={TransportId.HTTP: t})
            caps = await client.discover_capabilities(StaticCapabilityProbe("night", "hdr"))
            assert caps.supported == frozenset({"night", "hdr"})

            assert (await client.camera.shutter(Shot(iso=1))).accepted  # ungated
            assert (await client.camera.night_photo(Shot(iso=2))).accepted  # night ok
            assert (await client.settings.hdr.write(Hdr(on=True))).accepted  # hdr ok

            with pytest.raises(CapabilityUnavailableError) as excinfo:
                await client.sensor.motion.subscribe().__anext__()
    assert excinfo.value.operation_id == "sensor.motion.subscribe"
    assert excinfo.value.missing == ("motion",)


async def test_gated_command_raises_when_tag_absent(tmp_path: Path) -> None:
    with _built(tmp_path, "absent", _CAP_MANIFEST):
        from absent_pkg.h import Shot
        from absent_sdk import AbsentClient, TransportId

        async with open_transport(_loopback()) as t:
            client = AbsentClient(transports={TransportId.HTTP: t})
            await client.discover_capabilities(StaticCapabilityProbe())  # empty
            with pytest.raises(CapabilityUnavailableError, match="camera.night_photo"):
                await client.camera.night_photo(Shot(iso=3))


async def test_custom_probe_dispatches_to_discover(tmp_path: Path) -> None:
    """A probe may dispatch commands through the client to read the device's tags."""
    with _built(tmp_path, "custom", _CAP_MANIFEST):
        from custom_pkg.h import InfoReq, Shot
        from custom_sdk import CustomClient, TransportId

        class InfoProbe:
            async def probe(self, client: CustomClient) -> frozenset[str]:
                info = await client.system.info(InfoReq())
                assert info is not None
                return frozenset(info.data.features)

        async with open_transport(_loopback()) as t:
            client = CustomClient(transports={TransportId.HTTP: t})
            caps = await client.discover_capabilities(InfoProbe())
            assert caps.supported == frozenset({"night", "hdr"})
            assert (await client.camera.night_photo(Shot(iso=4))).accepted


def test_sync_discover_and_gating(tmp_path: Path) -> None:
    with _built(tmp_path, "syncap", _CAP_MANIFEST):
        from syncap_pkg.h import Shot
        from syncap_sdk import SyncSyncapClient, TransportId

        transport = _loopback()
        asyncio.run(transport.open())
        try:
            client = SyncSyncapClient(transports={TransportId.HTTP: transport})
            client.discover_capabilities(StaticCapabilityProbe("night"))
            assert client.camera.night_photo(Shot(iso=5)).accepted
            with pytest.raises(CapabilityUnavailableError):
                client.settings.hdr.read()  # hdr not in the discovered set
        finally:
            asyncio.run(transport.close())


# ---------------------------------------------------------------------------
# Profile pipeline
# ---------------------------------------------------------------------------


def test_capability_survives_profile_build(tmp_path: Path) -> None:
    manifest_path = _write_sources(tmp_path, "prof", _CAP_MANIFEST)
    profiles = tmp_path / "prof" / "audience_profiles.yaml"
    profiles.write_text("profiles:\n  internal:\n    include_audience: [internal]\n", encoding="utf-8")
    result = build_sdk(
        manifest_path,
        output_root=tmp_path / "prof" / "out",
        profile="internal",
        profiles_path=profiles,
        clean=True,
    )
    client_src = (result.package_path / "client.py").read_text(encoding="utf-8")
    assert '"camera.night_photo": ("night",),' in client_src


# ---------------------------------------------------------------------------
# Reference example (pneumatic_bear_poker): logs.download gated by "diagnostics"
# ---------------------------------------------------------------------------

_EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "pneumatic_bear_poker"


@contextmanager
def _example_on_path(tmp_path: Path) -> Iterator[Path]:
    snapshot = set(sys.modules)
    result = build_sdk(_EXAMPLE_DIR / "manifest.yaml", output_root=tmp_path)
    added = [str(_EXAMPLE_DIR / "src"), str(tmp_path)]
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


async def test_example_logs_download_gated_by_diagnostics(tmp_path: Path) -> None:
    """The reference SDK gates ``logs.download`` on the ``diagnostics`` tag."""
    with _example_on_path(tmp_path):
        from devices.pneumatic_bear_poker.handlers.diagnostics import DownloadLogsRequest
        from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

        def _logs(req: HttpRequest) -> HttpResponse:
            return HttpResponse(status=200, body=json.dumps({"lines": ["boot"], "next_sequence": 1}).encode())

        async with open_transport(LoopbackTransport(_logs)) as t:
            client = PneumaticBearPokerClient(transports={TransportId.HTTP: t})
            # Ungated before discovery.
            assert (await client.logs.download(DownloadLogsRequest())).accepted
            # A device whose probe lacks "diagnostics" cannot download logs.
            await client.discover_capabilities(StaticCapabilityProbe())
            with pytest.raises(CapabilityUnavailableError, match="logs.download"):
                await client.logs.download(DownloadLogsRequest())
            # A diagnostics-capable device can.
            await client.discover_capabilities(StaticCapabilityProbe("diagnostics"))
            assert (await client.logs.download(DownloadLogsRequest())).accepted

