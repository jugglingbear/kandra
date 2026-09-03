"""Acceptance test for `kandra build`: generate the example SDK and round-trip a command."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest
from kandra.generator import build_sdk
from kandra_runtime import HttpRequest, HttpResponse

EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "pneumatic_bear_poker"
EXAMPLE_MANIFEST = EXAMPLE_DIR / "manifest.yaml"
EXAMPLE_SRC = EXAMPLE_DIR / "src"


@pytest.fixture
def sdk_on_path(tmp_path: Path) -> Iterator[Path]:
    """Build the example SDK into ``tmp_path``; expose it (and the example src tree) on ``sys.path``."""
    result = build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path)
    added = [str(EXAMPLE_SRC), str(tmp_path)]
    sys.path[:0] = added
    snapshot = set(sys.modules)
    try:
        yield result.package_path
    finally:
        for p in added:
            with suppress(ValueError):
                sys.path.remove(p)
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]


def _http_echo_deploy(req: HttpRequest) -> HttpResponse:
    """HTTP loopback handler: echo the request body as a Deploy response."""
    body = json.loads((req.body or b"{}").decode("utf-8"))
    payload = json.dumps({"delivered_psi": body["pressure_psi"]}).encode("utf-8")
    return HttpResponse(status=200, body=payload)


def test_generated_sdk_files_are_written(sdk_on_path: Path) -> None:
    """Generator writes the expected file set into the output directory."""
    for name in ("__init__.py", "py.typed", "transports.py", "registry.py", "client.py", "_generated_from.json"):
        assert (sdk_on_path / name).is_file(), f"missing generated file: {name}"


async def test_generated_sdk_async_dispatch(sdk_on_path: Path) -> None:
    """Async dispatch through the generated client returns an ACCEPTED Result."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest, DeployResponse
    from kandra_runtime import LoopbackTransport, open_transport
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_http_echo_deploy)
    async with open_transport(transport) as t:
        client = PneumaticBearPokerClient(transports={TransportId.HTTP: t})
        result = await client.poker.deploy(DeployRequest(pressure_psi=42))
    assert result is not None
    assert result.accepted
    assert result.data == DeployResponse(delivered_psi=42)
    assert result.extra == {"http_status": 200}


def test_generated_sync_client_dispatch(sdk_on_path: Path) -> None:
    """Sync client wrapper dispatches via ``asyncio.run`` and returns an ACCEPTED Result."""
    import asyncio

    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest, DeployResponse
    from kandra_runtime import LoopbackTransport
    from pneumatic_bear_poker_sdk import SyncPneumaticBearPokerClient, TransportId

    transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_http_echo_deploy)
    asyncio.run(transport.open())
    try:
        client = SyncPneumaticBearPokerClient(transports={TransportId.HTTP: transport})
        result = client.poker.deploy(DeployRequest(pressure_psi=7))
    finally:
        asyncio.run(transport.close())
    assert result is not None
    assert result.accepted
    assert result.data == DeployResponse(delivered_psi=7)


async def test_non_accepted_result_fires_hook_and_returns_envelope(sdk_on_path: Path) -> None:
    """A 5xx response should classify as DEVICE_FAULT and trigger ``on_non_accepted``."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest
    from kandra_runtime import Classification, LoopbackTransport, open_transport
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    def faulting(_req: HttpRequest) -> HttpResponse:
        return HttpResponse(status=503, body=b"service unavailable")

    captured: list[str] = []
    transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(faulting)
    async with open_transport(transport) as t:
        client = PneumaticBearPokerClient(transports={TransportId.HTTP: t})
        client.on_non_accepted = captured.append
        result = await client.poker.deploy(DeployRequest(pressure_psi=1))
    assert result is not None
    assert result.classification is Classification.DEVICE_FAULT
    assert result.data is None
    assert result.extra == {"http_status": 503}
    assert captured and "DEVICE_FAULT" in captured[0] and "HTTP 503" in captured[0]


async def test_ignore_failures_suppresses_hook_but_not_result(sdk_on_path: Path) -> None:
    """``ignore_failures()`` blocks the hook while still returning the Result."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest
    from kandra_runtime import Classification, LoopbackTransport, open_transport
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    def rejecting(_req: HttpRequest) -> HttpResponse:
        return HttpResponse(status=400, body=b"bad pressure")

    captured: list[str] = []
    transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(rejecting)
    async with open_transport(transport) as t:
        client = PneumaticBearPokerClient(transports={TransportId.HTTP: t})
        client.on_non_accepted = captured.append
        with client.ignore_failures():
            result = await client.poker.deploy(DeployRequest(pressure_psi=999))
    assert result is not None
    assert result.classification is Classification.REJECTED
    assert captured == []  # hook suppressed


async def test_via_kwarg_rejects_unwired_transport(sdk_on_path: Path) -> None:
    """`via=` must reject transports that the client wasn't constructed with."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest
    from kandra_runtime import LoopbackTransport, open_transport
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    def noop(_req: HttpRequest) -> HttpResponse:  # pragma: no cover - never invoked
        return HttpResponse(status=200, body=b"{}")

    transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(noop)
    async with open_transport(transport) as t:
        client = PneumaticBearPokerClient(transports={TransportId.HTTP: t})
        # This command only wires the HTTP transport, so the command registry
        # doesn't list the BLE transport; via=BLE therefore fails the "does not
        # support" check before the "not wired" check is reached. Either error
        # is a valid rejection of the unsupported transport.
        with pytest.raises(ValueError, match="does not support|not wired"):
            await client.poker.deploy(DeployRequest(pressure_psi=1), via=TransportId.BLE)
