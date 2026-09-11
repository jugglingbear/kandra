"""Backfill tests for previously-uncovered runtime error/edge branches.

Targets the failure and edge paths in the BLE/HTTP transports, scanners,
enrollment adapters, and result formatting that the happy-path suites don't
exercise. Uses in-process fakes only — no real bleak or network dependency.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest
from kandra_runtime import (
    BleEnrollment,
    BleIdentity,
    BleRequest,
    BleScanner,
    BleTransport,
    Candidate,
    Classification,
    EnrollmentError,
    HttpEnrollment,
    HttpRequest,
    HttpScanner,
    HttpTransport,
    Result,
    TransportError,
    TransportTimeoutError,
    format_failure,
    open_transport,
)
from kandra_runtime.enrollment import _BleTransportContextAdapter, _default_ble_transport_factory

_CH = {
    "command": ("aaaa0001-0000-0000-0000-000000000001", "aaaa0001-0000-0000-0000-000000000002"),
    "query": ("aaaa0002-0000-0000-0000-000000000001", "aaaa0002-0000-0000-0000-000000000002"),
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Echo-on-write in-process stand-in for ``bleak.BleakClient``."""

    def __init__(
        self,
        address: str,
        *,
        connect_delay: float = 0.0,
        connect_error: Exception | None = None,
        fail_notify_on: str | None = None,
    ) -> None:
        self.address = address
        self._connected = False
        self.callbacks: dict[str, Any] = {}
        self.wire: dict[str, str] = {}
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.stop_notify_calls: list[str] = []
        self._connect_delay = connect_delay
        self._connect_error = connect_error
        self._fail_notify_on = fail_notify_on

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_error is not None:
            raise self._connect_error
        if self._connect_delay:
            await asyncio.sleep(self._connect_delay)
        self._connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False
        self.callbacks.clear()

    async def start_notify(self, char: Any, callback: Any) -> None:
        if self._fail_notify_on is not None and str(char) == self._fail_notify_on:
            raise RuntimeError("start_notify boom")
        self.callbacks[str(char)] = callback

    async def stop_notify(self, char: Any) -> None:
        self.stop_notify_calls.append(str(char))

    async def write_gatt_char(self, char: Any, data: bytes, response: bool = False) -> None:
        notify = self.wire.get(str(char))
        if notify is not None and notify in self.callbacks:
            self.callbacks[notify](0, bytearray(data))


def _wired_fake(**kwargs: Any) -> _FakeClient:
    fake = _FakeClient("AA:BB:CC:DD:EE:FF", **kwargs)
    for write_uuid, notify_uuid in _CH.values():
        fake.wire[write_uuid] = notify_uuid
    return fake


def _factory(fake: _FakeClient):  # type: ignore[no-untyped-def]
    return lambda _address: fake


# ---------------------------------------------------------------------------
# BleTransport
# ---------------------------------------------------------------------------


def test_ble_transport_from_identity_builds_transport() -> None:
    ident = BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF")
    fake = _wired_fake()
    transport = BleTransport.from_identity(ident, channels=_CH, client_factory=_factory(fake))
    assert isinstance(transport, BleTransport)
    assert transport._address == "AA:BB:CC:DD:EE:FF"


def test_ble_default_client_factory_imports_bleak(monkeypatch: pytest.MonkeyPatch) -> None:
    from kandra_runtime.ble_transport import _default_client_factory

    class _StubClient:
        def __init__(self, address: str) -> None:
            self.address = address

    fake_bleak = types.ModuleType("bleak")
    fake_bleak.BleakClient = _StubClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bleak", fake_bleak)
    client = _default_client_factory("AA:BB:CC:DD:EE:FF")
    assert client.address == "AA:BB:CC:DD:EE:FF"  # type: ignore[attr-defined]


async def test_ble_open_when_already_open_is_noop() -> None:
    fake = _wired_fake()
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CH, client_factory=_factory(fake))
    await transport.open()
    await transport.open()  # second open short-circuits
    assert fake.connect_calls == 1
    await transport.close()


async def test_ble_connect_timeout_raises() -> None:
    fake = _wired_fake(connect_delay=0.5)
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CH, client_factory=_factory(fake), connect_timeout=0.01)
    with pytest.raises(TransportTimeoutError, match="timed out"):
        await transport.open()


async def test_ble_start_notify_failure_rolls_back() -> None:
    # Fail on the *second* channel's notify so rollback must stop the first.
    fake = _wired_fake(fail_notify_on=_CH["query"][1])
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CH, client_factory=_factory(fake))
    with pytest.raises(TransportError, match="start_notify"):
        await transport.open()
    assert fake.disconnect_calls >= 1
    assert _CH["command"][1] in fake.stop_notify_calls


async def test_ble_close_when_never_opened_is_noop() -> None:
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CH, client_factory=_factory(_wired_fake()))
    await transport.close()  # no client yet -> early return, no error


async def test_ble_request_drains_stale_notifications() -> None:
    fake = _wired_fake()
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CH, client_factory=_factory(fake))
    await transport.open()
    try:
        # An unsolicited notification arrives before the next request.
        fake.callbacks[_CH["command"][1]](0, bytearray(b"stale"))
        reply = await transport.request(BleRequest(channel="command", payload=b"real"))
        assert reply == b"real"  # stale byte was drained, not returned
    finally:
        await transport.close()


# ---------------------------------------------------------------------------
# BleScanner
# ---------------------------------------------------------------------------


class _FakeBleakScanner:
    def __init__(self, callback: Any, *, devices: tuple[Any, ...] = (), start_error: Exception | None = None) -> None:
        self._callback = callback
        self._devices = devices
        self._start_error = start_error
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error
        self.started = True
        for device, adv in self._devices:
            self._callback(device, adv)

    async def stop(self) -> None:
        self.stopped = True


class _Dev:
    def __init__(self, address: str, name: str | None = None) -> None:
        self.address = address
        self.name = name


class _Adv:
    def __init__(self) -> None:
        self.local_name = "PBP-001"
        self.rssi = -40
        self.service_uuids = ("b5f90000-aa8d-11e3-9046-0002a5d5c51b",)
        self.manufacturer_data: dict[int, bytes] = {}


def test_ble_scanner_construct_failure_raises() -> None:
    def boom(_cb: Any) -> Any:
        raise RuntimeError("no adapter")

    scanner = BleScanner(scanner_factory=boom)
    with pytest.raises(TransportError, match="failed to construct"):
        scanner.scan()


async def test_ble_scanner_start_failure_raises() -> None:
    scanner = BleScanner(scanner_factory=lambda cb: _FakeBleakScanner(cb, start_error=RuntimeError("radio off")))
    with pytest.raises(TransportError, match="failed to start"):
        [candidate async for candidate in scanner.scan(timeout=0.2)]


async def test_ble_scanner_yields_candidate_without_timeout() -> None:
    devices = ((_Dev("AA:01"), _Adv()),)
    scanner = BleScanner(scanner_factory=lambda cb: _FakeBleakScanner(cb, devices=devices))
    iterator = scanner.scan(timeout=None)
    candidate = await anext(iterator)  # exercises the timeout-less queue.get() path
    assert candidate.address == "AA:01"
    await iterator.aclose()


async def test_ble_scanner_timeout_returns_empty() -> None:
    scanner = BleScanner(scanner_factory=lambda cb: _FakeBleakScanner(cb, devices=()))
    result = [candidate async for candidate in scanner.scan(timeout=0.05)]
    assert result == []


def test_ble_scanner_default_factory_imports_bleak(monkeypatch: pytest.MonkeyPatch) -> None:
    from kandra_runtime.ble_scanner import _default_scanner_factory

    class _StubScanner:
        def __init__(self, *, detection_callback: Any = None) -> None:
            self.detection_callback = detection_callback

    fake_bleak = types.ModuleType("bleak")
    fake_bleak.BleakScanner = _StubScanner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bleak", fake_bleak)
    built = _default_scanner_factory(lambda _d, _a: None)
    assert isinstance(built, _StubScanner)


# ---------------------------------------------------------------------------
# HttpScanner
# ---------------------------------------------------------------------------


class _DummySession:
    async def close(self) -> None:
        return None


def test_http_scanner_session_factory_failure_raises() -> None:
    def boom() -> Any:
        raise RuntimeError("no session")

    scanner = HttpScanner(["http://x"], session_factory=boom)

    async def _run() -> list[Candidate]:
        return [candidate async for candidate in scanner.scan()]

    with pytest.raises(TransportError, match="failed to construct aiohttp session"):
        asyncio.run(_run())


async def test_http_scanner_probe_exception_yields_nothing() -> None:
    async def boom_probe(_session: Any, _url: str) -> Candidate | None:
        raise RuntimeError("probe blew up")

    scanner = HttpScanner(
        ["http://x"],
        probe_callable=boom_probe,
        session_factory=lambda: _DummySession(),  # type: ignore[arg-type,return-value]
    )
    result = [candidate async for candidate in scanner.scan()]
    assert result == []


async def test_http_scanner_timeout_cancels_pending_probe() -> None:
    async def slow_probe(_session: Any, _url: str) -> Candidate | None:
        await asyncio.sleep(1.0)
        return Candidate(transport="http", address="http://x")

    scanner = HttpScanner(
        ["http://x"],
        probe_callable=slow_probe,
        session_factory=lambda: _DummySession(),  # type: ignore[arg-type,return-value]
    )
    result = [candidate async for candidate in scanner.scan(timeout=0.05)]
    assert result == []  # deadline hit before the slow probe resolved


async def test_http_scanner_zero_timeout_returns_immediately() -> None:
    async def slow_probe(_session: Any, _url: str) -> Candidate | None:
        await asyncio.sleep(1.0)
        return Candidate(transport="http", address="http://x")

    scanner = HttpScanner(
        ["http://x"],
        probe_callable=slow_probe,
        session_factory=lambda: _DummySession(),  # type: ignore[arg-type,return-value]
    )
    # A zero deadline is already expired on the first drain check.
    result = [candidate async for candidate in scanner.scan(timeout=0.0)]
    assert result == []


# ---------------------------------------------------------------------------
# HttpTransport
# ---------------------------------------------------------------------------


async def test_http_request_connection_error_wraps_transport_error() -> None:
    transport = HttpTransport(base_url="http://127.0.0.1:1")
    async with open_transport(transport):
        with pytest.raises(TransportError):
            await transport.request(HttpRequest(method="GET", path="/"))


# ---------------------------------------------------------------------------
# Enrollment internals
# ---------------------------------------------------------------------------


async def test_ble_transport_context_adapter_opens_and_closes() -> None:
    class _FakeTransport:
        def __init__(self) -> None:
            self.opened = False
            self.closed = False

        async def open(self) -> None:
            self.opened = True

        async def close(self) -> None:
            self.closed = True

    fake = _FakeTransport()
    adapter = _BleTransportContextAdapter(fake)  # type: ignore[arg-type]
    async with adapter:
        assert fake.opened
    assert fake.closed


def test_default_ble_transport_factory_returns_adapter() -> None:
    adapter = _default_ble_transport_factory(
        "AA:BB:CC:DD:EE:FF", {"probe": ("aaaa0001-0000-0000-0000-000000000001", "aaaa0001-0000-0000-0000-000000000002")}
    )
    assert isinstance(adapter, _BleTransportContextAdapter)


async def test_ble_enrollment_wraps_transport_error() -> None:
    class _TransportErrEnter:
        def __init__(self, _address: str, _channels: Any) -> None: ...

        async def __aenter__(self) -> Any:
            raise TransportError("link down")

        async def __aexit__(self, *_exc: Any) -> None: ...

    enrollment = BleEnrollment(transport_factory=_TransportErrEnter)  # type: ignore[arg-type]
    candidate = Candidate(transport="ble", address="AA:BB:CC:DD:EE:FF")
    with pytest.raises(EnrollmentError, match="failed to bond"):
        await enrollment.enroll(candidate, saved_name="poker")


async def test_http_enrollment_connection_error_raises() -> None:
    enrollment = HttpEnrollment(login_path="/login", login_payload=lambda _c: {})
    candidate = Candidate(transport="http", address="http://127.0.0.1:1")
    with pytest.raises(EnrollmentError, match="login"):
        await enrollment.enroll(candidate, saved_name="cloud")


class _FakeResp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def json(self, content_type: Any = None) -> Any:
        return self._payload


class _FakeSession:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp
        self.closed = False

    def post(self, _url: str, json: Any = None) -> _FakeResp:
        return self._resp

    async def close(self) -> None:
        self.closed = True


async def test_http_enrollment_non_mapping_response_raises() -> None:
    session = _FakeSession(_FakeResp(200, [1, 2, 3]))
    enrollment = HttpEnrollment(
        login_path="/login",
        login_payload=lambda _c: {},
        session_factory=lambda: session,  # type: ignore[arg-type,return-value]
    )
    candidate = Candidate(transport="http", address="http://device")
    with pytest.raises(EnrollmentError, match="not a JSON object"):
        await enrollment.enroll(candidate, saved_name="cloud")
    assert session.closed


async def test_http_enrollment_default_empty_payload_when_login_payload_none() -> None:
    session = _FakeSession(_FakeResp(200, {"token": "t-123"}))
    enrollment = HttpEnrollment(
        login_path="/login",  # set, but no login_payload -> defaults to {}
        session_factory=lambda: session,  # type: ignore[arg-type,return-value]
    )
    candidate = Candidate(transport="http", address="http://device")
    ident = await enrollment.enroll(candidate, saved_name="cloud")
    assert ident.auth_token == "t-123"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


def test_format_failure_includes_reason() -> None:
    result = Result[int](classification=Classification.REJECTED, reason="bad params")
    summary = format_failure(result)
    assert summary.startswith("REJECTED")
    assert "bad params" in summary


def test_format_failure_accepted_returns_empty() -> None:
    result = Result[int](classification=Classification.ACCEPTED, data=1)
    assert format_failure(result) == ""
