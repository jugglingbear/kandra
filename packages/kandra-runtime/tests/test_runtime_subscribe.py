"""Tests for the `Subscribable` transport primitive (M9 slice 9a)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from kandra_runtime import (
    BleRequest,
    BleTransport,
    Classification,
    ClassificationVerdict,
    CodecError,
    Command,
    HttpRequest,
    HttpTransport,
    LoopbackTransport,
    Subscribable,
    TransportError,
    TransportNotOpenError,
    TransportTimeoutError,
    always_accepted_interpreter,
    dispatch_subscribe,
    open_transport,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


async def _three_updates(_envelope: bytes) -> AsyncIterator[bytes]:
    for i in range(3):
        yield f"update-{i}".encode()


def _echo(envelope: bytes) -> bytes:
    return envelope


async def test_loopback_subscribe_yields_scripted_stream() -> None:
    transport = LoopbackTransport(_echo, subscribe_handler=_three_updates)
    async with open_transport(transport):
        received = [item async for item in transport.subscribe(b"settings.resolution")]
    assert received == [b"update-0", b"update-1", b"update-2"]


def test_loopback_without_subscribe_handler_raises() -> None:
    transport = LoopbackTransport(_echo)
    with pytest.raises(TransportError, match="without a subscribe_handler"):
        transport.subscribe(b"x")


async def test_loopback_subscribe_before_open_raises() -> None:
    transport = LoopbackTransport(_echo, subscribe_handler=_three_updates)
    with pytest.raises(TransportNotOpenError):
        _ = [item async for item in transport.subscribe(b"x")]


async def test_loopback_subscribe_can_be_closed_early() -> None:
    async def many(_envelope: bytes) -> AsyncIterator[bytes]:
        for i in range(1000):
            yield str(i).encode()

    transport = LoopbackTransport(_echo, subscribe_handler=many)
    async with open_transport(transport):
        stream = transport.subscribe(b"x")
        first = await anext(stream)
        await stream.aclose()  # tears the subscription down early
    assert first == b"0"


def test_loopback_satisfies_subscribable_protocol() -> None:
    transport = LoopbackTransport(_echo, subscribe_handler=_three_updates)
    assert isinstance(transport, Subscribable)


# ---------------------------------------------------------------------------
# HTTP SSE subscribe
# ---------------------------------------------------------------------------


async def _make_sse_server(body_writer: Callable[[web.StreamResponse], Any]) -> TestServer:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await body_writer(resp)
        await resp.write_eof()
        return resp

    async def deny(_request: web.Request) -> web.Response:
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/events", handler)
    app.router.add_get("/missing", deny)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_http_subscribe_yields_sse_events() -> None:
    async def write_three(resp: web.StreamResponse) -> None:
        for i in range(3):
            await resp.write(f"data: value-{i}\n\n".encode())

    server = await _make_sse_server(write_three)
    try:
        async with HttpTransport(str(server.make_url("/"))) as transport:
            bodies = [r.body async for r in transport.subscribe(HttpRequest(method="GET", path="/events"))]
        assert bodies == [b"value-0", b"value-1", b"value-2"]
    finally:
        await server.close()


async def test_http_subscribe_joins_multiline_data() -> None:
    async def write_multiline(resp: web.StreamResponse) -> None:
        await resp.write(b"data: line-a\ndata: line-b\n\n")

    server = await _make_sse_server(write_multiline)
    try:
        async with HttpTransport(str(server.make_url("/"))) as transport:
            bodies = [r.body async for r in transport.subscribe(HttpRequest(method="GET", path="/events"))]
        assert bodies == [b"line-a\nline-b"]
    finally:
        await server.close()


async def test_http_subscribe_error_status_raises() -> None:
    async def _noop(_resp: web.StreamResponse) -> None:
        return None

    server = await _make_sse_server(_noop)
    try:
        async with HttpTransport(str(server.make_url("/"))) as transport:
            with pytest.raises(TransportError, match="returned 404"):
                _ = [r async for r in transport.subscribe(HttpRequest(method="GET", path="/missing"))]
    finally:
        await server.close()


async def test_http_subscribe_before_open_raises() -> None:
    transport = HttpTransport("http://127.0.0.1:1/")
    with pytest.raises(TransportNotOpenError):
        _ = [r async for r in transport.subscribe(HttpRequest(method="GET", path="/events"))]


async def test_http_subscribe_flushes_trailing_event() -> None:
    async def write_no_trailing_blank(resp: web.StreamResponse) -> None:
        await resp.write(b"data: only\n")  # EOF with no terminating blank line

    server = await _make_sse_server(write_no_trailing_blank)
    try:
        async with HttpTransport(str(server.make_url("/"))) as transport:
            bodies = [r.body async for r in transport.subscribe(HttpRequest(method="GET", path="/events"))]
        assert bodies == [b"only"]
    finally:
        await server.close()


async def test_http_subscribe_connection_error_raises() -> None:
    transport = HttpTransport("http://127.0.0.1:1/")
    async with open_transport(transport):
        with pytest.raises(TransportError):
            _ = [r async for r in transport.subscribe(HttpRequest(method="GET", path="/events"))]


async def test_http_subscribe_timeout_raises() -> None:
    async def hang(_resp: web.StreamResponse) -> None:
        await asyncio.sleep(2.0)  # headers sent by prepare(); body never arrives

    server = await _make_sse_server(hang)
    try:
        async with HttpTransport(str(server.make_url("/")), timeout=0.2) as transport:
            with pytest.raises(TransportTimeoutError):
                _ = [r async for r in transport.subscribe(HttpRequest(method="GET", path="/events"))]
    finally:
        await server.close()


def test_http_transport_is_subscribable() -> None:
    assert isinstance(HttpTransport("http://x/"), Subscribable)


# ---------------------------------------------------------------------------
# BLE Notify subscribe
# ---------------------------------------------------------------------------

_BLE_CHANNELS = {"telemetry": ("aaaa0001-0000-0000-0000-000000000001", "aaaa0001-0000-0000-0000-000000000002")}


class _FakeBle:
    def __init__(self, _address: str) -> None:
        self._connected = False
        self.callbacks: dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self.callbacks.clear()

    async def start_notify(self, char: Any, callback: Any) -> None:
        self.callbacks[str(char)] = callback

    async def stop_notify(self, char: Any) -> None:
        self.callbacks.pop(str(char), None)

    async def write_gatt_char(self, char: Any, data: bytes, response: bool = False) -> None:
        return None


async def test_ble_subscribe_yields_notifications() -> None:
    fake = _FakeBle("AA:BB:CC:DD:EE:FF")
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_BLE_CHANNELS, client_factory=lambda _a: fake)
    await transport.open()
    try:
        notify_uuid = _BLE_CHANNELS["telemetry"][1]
        stream = transport.subscribe(BleRequest(channel="telemetry", payload=b""))
        fake.callbacks[notify_uuid](0, bytearray(b"n1"))
        fake.callbacks[notify_uuid](0, bytearray(b"n2"))
        assert await anext(stream) == b"n1"
        assert await anext(stream) == b"n2"
        await stream.aclose()
    finally:
        await transport.close()


async def test_ble_subscribe_unknown_channel_raises() -> None:
    fake = _FakeBle("AA:BB:CC:DD:EE:FF")
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_BLE_CHANNELS, client_factory=lambda _a: fake)
    await transport.open()
    try:
        with pytest.raises(TransportError, match="not declared on transport"):
            _ = [item async for item in transport.subscribe(BleRequest(channel="nope", payload=b""))]
    finally:
        await transport.close()


async def test_ble_subscribe_before_open_raises() -> None:
    transport = BleTransport("AA:BB:CC:DD:EE:FF", channels=_BLE_CHANNELS, client_factory=_FakeBle)
    with pytest.raises(TransportNotOpenError):
        _ = [item async for item in transport.subscribe(BleRequest(channel="telemetry", payload=b""))]


# ---------------------------------------------------------------------------
# dispatch_subscribe (Result-per-update over a Subscribable)
# ---------------------------------------------------------------------------


class _CountCodec:
    """Encodes a topic string to bytes; decodes byte updates to ints."""

    def encode(self, request: str) -> bytes:
        return request.encode()

    def decode(self, response: bytes) -> int:
        try:
            return int(response)
        except ValueError as exc:
            raise CodecError(f"not an int: {response!r}") from exc


class _NoSubTransport:
    """A minimal Transport with no subscribe() -- not Subscribable."""

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @property
    def is_open(self) -> bool:
        return True

    async def request(self, envelope: bytes) -> bytes:
        return envelope


def _count_command() -> Command[str, int, bytes, bytes]:
    return Command(id="telemetry.count", codec=_CountCodec(), interpreter=always_accepted_interpreter)


async def test_dispatch_subscribe_yields_decoded_results() -> None:
    async def updates(_env: bytes) -> AsyncIterator[bytes]:
        for i in range(3):
            yield str(i).encode()

    transport = LoopbackTransport(_echo, subscribe_handler=updates)
    async with open_transport(transport):
        results = [r async for r in dispatch_subscribe(_count_command(), transport, "topic")]
    assert all(r.accepted for r in results)
    assert [r.data for r in results] == [0, 1, 2]


async def test_dispatch_subscribe_requires_subscribable_transport() -> None:
    with pytest.raises(TransportError, match="does not support subscribe"):
        _ = [r async for r in dispatch_subscribe(_count_command(), _NoSubTransport(), "topic")]


async def test_dispatch_subscribe_decode_error_is_anomalous() -> None:
    async def updates(_env: bytes) -> AsyncIterator[bytes]:
        yield b"7"
        yield b"not-an-int"

    transport = LoopbackTransport(_echo, subscribe_handler=updates)
    async with open_transport(transport):
        results = [r async for r in dispatch_subscribe(_count_command(), transport, "topic")]
    assert results[0].accepted and results[0].data == 7
    assert results[1].classification is Classification.ANOMALOUS


async def test_dispatch_subscribe_midstream_failure_ends_with_transport_failure() -> None:
    async def updates(_env: bytes) -> AsyncIterator[bytes]:
        yield b"1"
        raise TransportError("link dropped")

    transport = LoopbackTransport(_echo, subscribe_handler=updates)
    async with open_transport(transport):
        results = [r async for r in dispatch_subscribe(_count_command(), transport, "topic")]
    assert results[0].accepted and results[0].data == 1
    assert results[-1].classification is Classification.TRANSPORT_FAILURE


async def test_dispatch_subscribe_surfaces_non_accepted_verdict() -> None:
    class _RejectInterpreter:
        def classify(self, response: bytes) -> ClassificationVerdict:
            return ClassificationVerdict(classification=Classification.REJECTED, reason="denied")

    async def updates(_env: bytes) -> AsyncIterator[bytes]:
        yield b"1"

    command: Command[str, int, bytes, bytes] = Command(
        id="telemetry.count", codec=_CountCodec(), interpreter=_RejectInterpreter()
    )
    transport = LoopbackTransport(_echo, subscribe_handler=updates)
    async with open_transport(transport):
        results = [r async for r in dispatch_subscribe(command, transport, "topic")]
    assert results[0].classification is Classification.REJECTED
    assert results[0].data is None
