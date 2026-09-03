"""Tests for the Scanner protocol, snapshot_scan helper, and HTTP/BLE adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from kandra_runtime import (
    BleScanner,
    Candidate,
    HttpScanner,
    Scanner,
    accept_all,
    snapshot_scan,
)

# ---------------------------------------------------------------------------
# A trivial in-process scanner for testing snapshot_scan / Scanner Protocol.
# ---------------------------------------------------------------------------


class _ScriptedScanner(Scanner):
    """Yields a pre-baked sequence of candidates one per `step` seconds."""

    def __init__(self, script: list[Candidate], *, step: float = 0.0) -> None:
        self._script = script
        self._step = step

    def scan(
        self,
        *,
        matcher: Callable[[Candidate], bool] = accept_all,
        timeout: float | None = None,  # - unused in scripted scanner
    ) -> AsyncIterator[Candidate]:
        async def _aiter() -> AsyncIterator[Candidate]:
            for candidate in self._script:
                if self._step:
                    await asyncio.sleep(self._step)
                if matcher(candidate):
                    yield candidate

        return _aiter()


def _candidate(transport: str, address: str, **kwargs: Any) -> Candidate:
    return Candidate(transport=transport, address=address, **kwargs)


# ---------------------------------------------------------------------------
# Scanner protocol + snapshot_scan helper.
# ---------------------------------------------------------------------------


async def test_snapshot_scan_collects_and_deduplicates() -> None:
    scanner = _ScriptedScanner(
        [
            _candidate("ble", "AA:01"),
            _candidate("ble", "AA:02"),
            _candidate("ble", "AA:01"),  # dup -> dropped
            _candidate("ble", "AA:03"),
        ],
    )
    result = await snapshot_scan(scanner, timeout=0.5)
    assert [c.address for c in result] == ["AA:01", "AA:02", "AA:03"]


async def test_snapshot_scan_applies_matcher() -> None:
    scanner = _ScriptedScanner(
        [
            _candidate("ble", "AA:01", advertised_name="PBP-001"),
            _candidate("ble", "AA:02", advertised_name="other"),
            _candidate("ble", "AA:03", advertised_name="PBP-002"),
        ],
    )
    result = await snapshot_scan(
        scanner,
        timeout=0.5,
        matcher=lambda c: (c.advertised_name or "").startswith("PBP-"),
    )
    assert [c.address for c in result] == ["AA:01", "AA:03"]


async def test_snapshot_scan_honors_timeout() -> None:
    scanner = _ScriptedScanner(
        [_candidate("ble", f"AA:{i:02X}") for i in range(10)],
        step=0.05,
    )
    result = await snapshot_scan(scanner, timeout=0.15)
    # 0.15 / 0.05 == 3 in theory, but timing is fuzzy -- accept 1..4.
    assert 1 <= len(result) <= 4


async def test_snapshot_scan_rejects_non_positive_timeout() -> None:
    scanner = _ScriptedScanner([])
    with pytest.raises(ValueError, match="timeout"):
        await snapshot_scan(scanner, timeout=0)


async def test_streaming_scan_yields_in_order() -> None:
    scanner = _ScriptedScanner(
        [_candidate("ble", "AA:01"), _candidate("ble", "AA:02"), _candidate("ble", "AA:03")],
    )
    got: list[str] = []
    async for c in scanner.scan(timeout=1.0):
        got.append(c.address)
    assert got == ["AA:01", "AA:02", "AA:03"]


# ---------------------------------------------------------------------------
# BleScanner (with a fake bleak scanner).
# ---------------------------------------------------------------------------


class _FakeBleDevice:
    def __init__(self, address: str, name: str | None = None) -> None:
        self.address = address
        self.name = name


class _FakeAdv:
    def __init__(
        self,
        local_name: str | None = None,
        rssi: int = -50,
        service_uuids: tuple[str, ...] = (),
        manufacturer_data: dict[int, bytes] | None = None,
    ) -> None:
        self.local_name = local_name
        self.rssi = rssi
        self.service_uuids = service_uuids
        self.manufacturer_data = manufacturer_data or {}


class _FakeBleakScanner:
    """Mimics bleak.BleakScanner: invokes the detection callback on start()."""

    def __init__(
        self,
        detection_callback: Callable[[Any, Any], None],
        *,
        script: list[tuple[_FakeBleDevice, _FakeAdv]],
    ) -> None:
        self._cb = detection_callback
        self._script = script
        self._started = False

    async def start(self) -> None:
        self._started = True
        # Schedule callbacks asynchronously so the consumer's iterator
        # is the one driving them.
        for device, adv in self._script:
            self._cb(device, adv)

    async def stop(self) -> None:
        self._started = False


def _ble_factory(script: list[tuple[_FakeBleDevice, _FakeAdv]]) -> Any:
    def factory(cb: Callable[[Any, Any], None]) -> _FakeBleakScanner:
        return _FakeBleakScanner(cb, script=script)

    return factory


async def test_ble_scanner_yields_candidates_with_metadata() -> None:
    script = [
        (_FakeBleDevice("AA:BB:01"), _FakeAdv(local_name="PBP-001", rssi=-42, service_uuids=("uuid-a",))),
        (_FakeBleDevice("AA:BB:02"), _FakeAdv(local_name="PBP-002", rssi=-55)),
    ]
    scanner = BleScanner(scanner_factory=_ble_factory(script))
    got = await snapshot_scan(scanner, timeout=0.2)
    assert {c.address for c in got} == {"AA:BB:01", "AA:BB:02"}
    pbp1 = next(c for c in got if c.address == "AA:BB:01")
    assert pbp1.advertised_name == "PBP-001"
    assert pbp1.metadata["rssi"] == -42
    assert pbp1.metadata["service_uuids"] == ("uuid-a",)


async def test_ble_scanner_dedups_repeated_advertisements() -> None:
    dev = _FakeBleDevice("AA:BB:01")
    script = [(dev, _FakeAdv()) for _ in range(5)]  # same device five times
    scanner = BleScanner(scanner_factory=_ble_factory(script))
    got = await snapshot_scan(scanner, timeout=0.2)
    assert [c.address for c in got] == ["AA:BB:01"]


async def test_ble_scanner_filters_via_matcher() -> None:
    script = [
        (_FakeBleDevice("AA:BB:01"), _FakeAdv(local_name="PBP-001")),
        (_FakeBleDevice("AA:BB:02"), _FakeAdv(local_name="something-else")),
    ]
    scanner = BleScanner(scanner_factory=_ble_factory(script))
    got = await snapshot_scan(
        scanner,
        timeout=0.2,
        matcher=lambda c: (c.advertised_name or "").startswith("PBP-"),
    )
    assert [c.address for c in got] == ["AA:BB:01"]


# ---------------------------------------------------------------------------
# HttpScanner against a live aiohttp test server.
# ---------------------------------------------------------------------------


@pytest.fixture
async def http_server():  # type: ignore[no-untyped-def]
    async def alive(_request: web.Request) -> web.Response:
        return web.Response(status=200, text="ok", headers={"Server": "test-server"})

    async def boom(_request: web.Request) -> web.Response:
        return web.Response(status=500, text="nope")

    app = web.Application()
    app.router.add_get("/", alive)
    app.router.add_get("/boom", boom)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    # Discover assigned port.
    sockets = site._server.sockets  # type: ignore[union-attr]
    assert sockets is not None
    port = sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


async def test_http_scanner_finds_alive_url(http_server: str) -> None:
    scanner = HttpScanner(
        [http_server, "http://127.0.0.1:1"],  # second URL: nothing listening
        per_probe_timeout=0.5,
    )
    got = await snapshot_scan(scanner, timeout=2.0)
    assert [c.address for c in got] == [http_server]
    assert got[0].advertised_name == "test-server"


async def test_http_scanner_skips_non_2xx(http_server: str) -> None:
    scanner = HttpScanner([http_server], probe_path="/boom", per_probe_timeout=0.5)
    got = await snapshot_scan(scanner, timeout=2.0)
    assert got == []


async def test_http_scanner_rejects_empty_candidates() -> None:
    with pytest.raises(ValueError, match="at least one"):
        HttpScanner([])


async def test_http_scanner_custom_probe_callable(http_server: str) -> None:
    async def custom(session: aiohttp.ClientSession, url: str) -> Candidate | None:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            return Candidate(
                transport="http", address=url, advertised_name="custom", metadata={"x": 1}
            )

    scanner = HttpScanner([http_server], probe_callable=custom, per_probe_timeout=0.5)
    got = await snapshot_scan(scanner, timeout=2.0)
    assert len(got) == 1
    assert got[0].advertised_name == "custom"
    assert got[0].metadata["x"] == 1
