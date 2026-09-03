"""BleTransport tests using an in-process FakeBleakClient.

Covers the BLE happy path (write → notify → bytes), per-channel routing,
serialization across concurrent same-channel requests, fire-and-forget
via expects_response=False, and the error paths (request before open,
unknown channel, connect timeout, write failure).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from kandra_runtime import (
    BleChannelCodec,
    BleRequest,
    BleTransport,
    Classification,
    Codec,
    Command,
    TransportError,
    TransportNotOpenError,
    always_accepted_interpreter,
    dispatch,
)

# ---------------------------------------------------------------------------
# FakeBleakClient — minimal in-process BLE stand-in for the BleakLike protocol.
# ---------------------------------------------------------------------------


class FakeBleakClient:
    """In-process fake for ``bleak.BleakClient``.

    Reflects writes back as a notification on the per-channel notify
    characteristic, unless a per-uuid override is registered via
    :meth:`respond_with` (e.g. for "echo the payload" or "raise on write").
    """

    def __init__(self, address: str) -> None:
        self.address = address
        self._connected = False
        self._notify_callbacks: dict[str, Callable[[int, bytearray], None]] = {}
        self._responder: dict[str, Callable[[bytes], bytes | None]] = {}
        # Map write_uuid -> notify_uuid so the fake knows where to send back data.
        self._wire: dict[str, str] = {}
        # Connect failure injection (set via constructor for test fixtures).
        self.fail_connect: bool = False
        self.fail_writes: bool = False
        # Track delivered writes for assertions.
        self.writes: list[tuple[str, bytes]] = []

    def wire(self, write_uuid: str, notify_uuid: str) -> None:
        """Tell the fake that writes on write_uuid produce notifies on notify_uuid."""
        self._wire[write_uuid] = notify_uuid

    def respond_with(self, write_uuid: str, fn: Callable[[bytes], bytes | None]) -> None:
        """Register a per-channel custom responder (returns reply bytes or None to skip)."""
        self._responder[write_uuid] = fn

    # -- BleakLike protocol surface ----------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if self.fail_connect:
            raise RuntimeError(f"FakeBleakClient.connect failed for {self.address!r}")
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self._notify_callbacks.clear()

    async def start_notify(self, char_specifier: Any, callback: Any) -> None:
        self._notify_callbacks[str(char_specifier)] = callback

    async def stop_notify(self, char_specifier: Any) -> None:
        self._notify_callbacks.pop(str(char_specifier), None)

    async def write_gatt_char(
        self,
        char_specifier: Any,
        data: bytes,
        response: bool = False,
    ) -> None:
        if self.fail_writes:
            raise RuntimeError("FakeBleakClient.write_gatt_char synthetic failure")
        write_uuid = str(char_specifier)
        self.writes.append((write_uuid, bytes(data)))
        # Default reply: echo. Custom responder may return None to skip the reply.
        responder = self._responder.get(write_uuid)
        reply: bytes | None
        reply = responder(bytes(data)) if responder is not None else bytes(data)
        if reply is None:
            return
        notify_uuid = self._wire.get(write_uuid)
        if notify_uuid is None:
            return
        callback = self._notify_callbacks.get(notify_uuid)
        if callback is None:
            return
        callback(0, bytearray(reply))


# ---------------------------------------------------------------------------
# Test channels and factory.
# ---------------------------------------------------------------------------

_CHANNELS = {
    "command": ("aaaa0001-0000-0000-0000-000000000001", "aaaa0001-0000-0000-0000-000000000002"),
    "query": ("aaaa0002-0000-0000-0000-000000000001", "aaaa0002-0000-0000-0000-000000000002"),
}


def _make_factory(fake: FakeBleakClient) -> Callable[[str], FakeBleakClient]:
    def factory(_address: str) -> FakeBleakClient:
        return fake

    return factory


def _wire_fake(fake: FakeBleakClient) -> None:
    for write_uuid, notify_uuid in _CHANNELS.values():
        fake.wire(write_uuid, notify_uuid)


@pytest.fixture
def fake() -> FakeBleakClient:
    f = FakeBleakClient("AA:BB:CC:DD:EE:FF")
    _wire_fake(f)
    return f


@pytest.fixture
async def transport(fake: FakeBleakClient):  # type: ignore[no-untyped-def]
    t = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CHANNELS, client_factory=_make_factory(fake))
    await t.open()
    try:
        yield t
    finally:
        await t.close()


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


async def test_request_echoes_payload_on_default_channel(transport: BleTransport) -> None:
    reply = await transport.request(BleRequest(channel="command", payload=b"\x01\x02\x03"))
    assert reply == b"\x01\x02\x03"


async def test_request_routes_per_channel(transport: BleTransport, fake: FakeBleakClient) -> None:
    fake.respond_with(_CHANNELS["query"][0], lambda _data: b"query-reply")
    fake.respond_with(_CHANNELS["command"][0], lambda _data: b"command-reply")
    assert await transport.request(BleRequest(channel="query", payload=b"q")) == b"query-reply"
    assert (
        await transport.request(BleRequest(channel="command", payload=b"c")) == b"command-reply"
    )
    # Each write hit the right write_uuid.
    written_uuids = [u for u, _ in fake.writes]
    assert _CHANNELS["query"][0] in written_uuids
    assert _CHANNELS["command"][0] in written_uuids


async def test_concurrent_same_channel_requests_serialize(
    transport: BleTransport, fake: FakeBleakClient
) -> None:
    counter = {"n": 0}

    def responder(data: bytes) -> bytes:
        counter["n"] += 1
        return data + str(counter["n"]).encode()

    fake.respond_with(_CHANNELS["command"][0], responder)
    results = await asyncio.gather(
        transport.request(BleRequest(channel="command", payload=b"a")),
        transport.request(BleRequest(channel="command", payload=b"b")),
        transport.request(BleRequest(channel="command", payload=b"c")),
    )
    # Each request got a distinct sequence-numbered reply, in order.
    assert results == [b"a1", b"b2", b"c3"]


# ---------------------------------------------------------------------------
# Lifecycle / error paths.
# ---------------------------------------------------------------------------


async def test_request_before_open_raises(fake: FakeBleakClient) -> None:
    t = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CHANNELS, client_factory=_make_factory(fake))
    with pytest.raises(TransportNotOpenError):
        await t.request(BleRequest(channel="command", payload=b"x"))


async def test_unknown_channel_raises(transport: BleTransport) -> None:
    with pytest.raises(TransportError, match="not declared"):
        await transport.request(BleRequest(channel="nope", payload=b"x"))


async def test_connect_failure_normalized(fake: FakeBleakClient) -> None:
    fake.fail_connect = True
    t = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CHANNELS, client_factory=_make_factory(fake))
    with pytest.raises(TransportError, match="connect"):
        await t.open()
    assert not t.is_open


async def test_write_failure_normalized(transport: BleTransport, fake: FakeBleakClient) -> None:
    fake.fail_writes = True
    with pytest.raises(TransportError, match="write_gatt_char"):
        await transport.request(BleRequest(channel="command", payload=b"x"))


async def test_close_clears_state(fake: FakeBleakClient) -> None:
    t = BleTransport("AA:BB:CC:DD:EE:FF", channels=_CHANNELS, client_factory=_make_factory(fake))
    await t.open()
    assert t.is_open
    await t.close()
    assert not t.is_open
    # Re-open should work.
    await t.open()
    assert t.is_open
    await t.close()


# ---------------------------------------------------------------------------
# Constructor validation.
# ---------------------------------------------------------------------------


def test_empty_address_rejected() -> None:
    with pytest.raises(ValueError, match="address"):
        BleTransport("", channels=_CHANNELS)


def test_empty_channels_rejected() -> None:
    with pytest.raises(ValueError, match="at least one channel"):
        BleTransport("AA:BB:CC:DD:EE:FF", channels={})


def test_malformed_channel_pair_rejected() -> None:
    with pytest.raises(ValueError, match="tuple"):
        BleTransport(
            "AA:BB:CC:DD:EE:FF",
            channels={"command": ("just-one-uuid",)},  # type: ignore[dict-item]
        )


def test_empty_uuid_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        BleTransport("AA:BB:CC:DD:EE:FF", channels={"command": ("", "x")})


# ---------------------------------------------------------------------------
# BleChannelCodec.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PingReq:
    n: int


@dataclass(frozen=True)
class _PingResp:
    n: int
    ok: bool


class _PingCodec:
    def __init__(self, req_type: type[_PingReq], resp_type: type[_PingResp]) -> None:
        self._req_type = req_type
        self._resp_type = resp_type

    def encode(self, request: _PingReq) -> bytes:
        return f"{request.n}".encode()

    def decode(self, payload: bytes) -> _PingResp:
        return _PingResp(n=int(payload.decode()), ok=True)


def test_ble_channel_codec_wraps_payload_codec() -> None:
    codec: Codec[_PingReq, _PingResp, BleRequest, bytes] = BleChannelCodec(
        channel="command",
        payload_codec=_PingCodec(_PingReq, _PingResp),
    )
    env = codec.encode(_PingReq(n=42))
    assert isinstance(env, BleRequest)
    assert env.channel == "command"
    assert env.payload == b"42"
    assert codec.decode(b"7") == _PingResp(n=7, ok=True)


def test_ble_channel_codec_empty_channel_rejected() -> None:
    with pytest.raises(ValueError, match="channel"):
        BleChannelCodec(channel="", payload_codec=_PingCodec(_PingReq, _PingResp))


# ---------------------------------------------------------------------------
# End-to-end through dispatch.
# ---------------------------------------------------------------------------


async def test_dispatch_routes_through_ble(
    transport: BleTransport, fake: FakeBleakClient
) -> None:
    fake.respond_with(_CHANNELS["command"][0], lambda _data: b"7")
    command: Command[_PingReq, _PingResp, BleRequest, bytes] = Command(
        id="ping",
        codec=BleChannelCodec(channel="command", payload_codec=_PingCodec(_PingReq, _PingResp)),
        interpreter=always_accepted_interpreter,
        timeout=1.0,
    )
    result = await dispatch(command, transport, _PingReq(n=42))
    assert result is not None
    assert result.classification is Classification.ACCEPTED
    assert result.data == _PingResp(n=7, ok=True)


async def test_dispatch_fire_and_forget_skips_decode(
    transport: BleTransport, fake: FakeBleakClient
) -> None:
    # No notify ever arrives — fire-and-forget should swallow the timeout.
    fake.respond_with(_CHANNELS["command"][0], lambda _data: None)
    command: Command[_PingReq, _PingResp, BleRequest, bytes] = Command(
        id="ping.no_response",
        codec=BleChannelCodec(channel="command", payload_codec=_PingCodec(_PingReq, _PingResp)),
        interpreter=always_accepted_interpreter,
        timeout=0.1,
        expects_response=False,
    )
    result = await dispatch(command, transport, _PingReq(n=0))
    assert result is None
    # The write still went out.
    assert any(w == _CHANNELS["command"][0] for w, _ in fake.writes)
