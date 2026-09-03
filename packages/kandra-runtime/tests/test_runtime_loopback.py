"""Acceptance tests for the runtime: hand-written command over loopback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from kandra_runtime import (
    Classification,
    Codec,
    Command,
    LoopbackTransport,
    TransportNotOpenError,
    always_accepted_interpreter,
    dispatch,
    dispatch_sync,
    open_transport,
)

# -- Hand-written example command -----------------------------------------------


@dataclass(frozen=True)
class BumpRequest:
    value: int


@dataclass(frozen=True)
class BumpResponse:
    value: int


class BumpCodec:
    """Trivial ASCII codec used as the loopback example."""

    def encode(self, request: BumpRequest) -> bytes:
        return str(request.value).encode("ascii")

    def decode(self, payload: bytes) -> BumpResponse:
        return BumpResponse(value=int(payload.decode("ascii")))


# `Codec` is a Protocol; `BumpCodec` should satisfy it structurally.
_codec_check: Codec[BumpRequest, BumpResponse, bytes, bytes] = BumpCodec()


def _bump_command(cid: str, timeout: float | None = None) -> Command[BumpRequest, BumpResponse, bytes, bytes]:
    return Command(
        id=cid,
        codec=BumpCodec(),
        interpreter=always_accepted_interpreter,
        timeout=timeout,
    )


def _assert_accepted(result: object, expected: BumpResponse) -> None:
    assert result is not None
    # Narrowing: we know dispatch returned Result for these tests.
    from kandra_runtime import Result

    assert isinstance(result, Result)
    assert result.classification is Classification.ACCEPTED
    assert result.data == expected


# -- Tests ----------------------------------------------------------------------


async def test_async_round_trip_with_async_handler() -> None:
    async def handler(payload: bytes) -> bytes:
        return str(int(payload) + 1).encode("ascii")

    cmd = _bump_command("bump.async")
    transport: LoopbackTransport[bytes, bytes] = LoopbackTransport(handler)

    async with open_transport(transport):
        result = await dispatch(cmd, transport, BumpRequest(value=41))

    _assert_accepted(result, BumpResponse(value=42))


async def test_async_round_trip_with_sync_handler() -> None:
    def handler(payload: bytes) -> bytes:
        return str(int(payload) * 2).encode("ascii")

    cmd = _bump_command("bump.sync")
    transport: LoopbackTransport[bytes, bytes] = LoopbackTransport(handler)

    async with open_transport(transport):
        result = await dispatch(cmd, transport, BumpRequest(value=21))

    _assert_accepted(result, BumpResponse(value=42))


def test_sync_dispatch() -> None:
    def handler(payload: bytes) -> bytes:
        return str(int(payload) + 100).encode("ascii")

    cmd = _bump_command("bump.plus100")
    transport: LoopbackTransport[bytes, bytes] = LoopbackTransport(handler)

    asyncio.run(transport.open())
    try:
        result = dispatch_sync(cmd, transport, BumpRequest(value=7))
    finally:
        asyncio.run(transport.close())

    _assert_accepted(result, BumpResponse(value=107))


async def test_request_on_closed_transport_yields_transport_failure() -> None:
    def handler(payload: bytes) -> bytes:
        return payload

    cmd = _bump_command("bump.echo")
    transport: LoopbackTransport[bytes, bytes] = LoopbackTransport(handler)

    # TransportNotOpenError is a TransportError subclass, so dispatch
    # catches it and reports TRANSPORT_FAILURE rather than propagating.
    result = await dispatch(cmd, transport, BumpRequest(value=1))
    assert result is not None
    assert result.classification is Classification.TRANSPORT_FAILURE
    # Sanity: the raw transport still raises when used directly.
    with pytest.raises(TransportNotOpenError):
        await transport.request(b"x")


async def test_timeout_yields_transport_failure_result() -> None:
    async def slow_handler(payload: bytes) -> bytes:
        await asyncio.sleep(0.5)
        return payload

    cmd = _bump_command("bump.slow", timeout=0.05)
    transport: LoopbackTransport[bytes, bytes] = LoopbackTransport(slow_handler)

    async with open_transport(transport):
        result = await dispatch(cmd, transport, BumpRequest(value=1))

    assert result is not None
    assert result.classification is Classification.TRANSPORT_FAILURE
    assert "bump.slow" in result.reason


async def test_no_timeout_means_no_wait_for() -> None:
    """A `None` timeout must not impose any deadline."""

    async def handler(payload: bytes) -> bytes:
        await asyncio.sleep(0.01)
        return payload

    cmd = _bump_command("bump.untimed")
    transport: LoopbackTransport[bytes, bytes] = LoopbackTransport(handler)

    async with open_transport(transport):
        result = await dispatch(cmd, transport, BumpRequest(value=5))

    _assert_accepted(result, BumpResponse(value=5))
