"""Acceptance tests: Result envelope + classification pipeline."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass

import pytest
from kandra_runtime import (
    AlwaysAcceptedResponseInterpreter,
    Classification,
    ClassificationVerdict,
    Codec,
    Command,
    DefaultHttpResponseInterpreter,
    HttpJsonCodec,
    HttpRequest,
    HttpResponse,
    LoopbackTransport,
    Result,
    dispatch,
    format_failure,
    open_transport,
)
from kandra_runtime.errors import CodecError

# ---------------------------------------------------------------------------
# Result + format_failure
# ---------------------------------------------------------------------------


def test_result_state_helpers() -> None:
    r = Result[int](classification=Classification.ACCEPTED, data=7)
    assert r.accepted and not r.rejected and not r.device_faulted
    assert not r.transport_failed and not r.anomalous
    assert r.data == 7

    r2 = Result[int](classification=Classification.REJECTED, reason="bad")
    assert r2.rejected and not r2.accepted
    assert r2.data is None


@pytest.mark.parametrize(
    "classification, ok, failed",
    [
        (Classification.ACCEPTED, True, False),
        (Classification.REJECTED, False, True),
        (Classification.DEVICE_FAULT, False, True),
        (Classification.TRANSPORT_FAILURE, False, True),
        (Classification.ANOMALOUS, False, True),
    ],
)
def test_result_ok_and_failed_aliases(
    classification: Classification, ok: bool, failed: bool
) -> None:
    """``ok`` and ``failed`` are complementary across all five classifications."""
    r = Result[int](classification=classification, data=None)
    assert r.ok is ok
    assert r.failed is failed
    # They must always disagree.
    assert r.ok is not r.failed
    # ``ok`` is exactly the same boolean as ``accepted``.
    assert r.ok is r.accepted


def test_result_is_frozen() -> None:
    r = Result[int](classification=Classification.ACCEPTED, data=1)
    with pytest.raises(FrozenInstanceError):
        r.reason = "nope"  # type: ignore[misc]


def test_format_failure_includes_classification_and_reason() -> None:
    r = Result[int](
        classification=Classification.DEVICE_FAULT,
        reason="HTTP 503",
        extra={"http_status": 503},
    )
    summary = format_failure(r)
    assert "DEVICE_FAULT" in summary
    assert "HTTP 503" in summary


# ---------------------------------------------------------------------------
# DefaultHttpResponseInterpreter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, Classification.ACCEPTED),
        (201, Classification.ACCEPTED),
        (204, Classification.ACCEPTED),
        (400, Classification.REJECTED),
        (404, Classification.REJECTED),
        (500, Classification.DEVICE_FAULT),
        (503, Classification.DEVICE_FAULT),
        (100, Classification.ANOMALOUS),
        (302, Classification.ANOMALOUS),
        (600, Classification.ANOMALOUS),
    ],
)
def test_default_http_interpreter(status: int, expected: Classification) -> None:
    c = DefaultHttpResponseInterpreter()
    verdict = c.classify(HttpResponse(status=status, body=b""))
    assert verdict.classification is expected
    assert verdict.extra == {"http_status": status}


def test_always_accepted_interpreter() -> None:
    c = AlwaysAcceptedResponseInterpreter()
    verdict = c.classify(object())
    assert verdict.classification is Classification.ACCEPTED


# ---------------------------------------------------------------------------
# dispatch end-to-end classification paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ping:
    pass


@dataclass(frozen=True)
class _Pong:
    value: int


def _http_command(handler_status: int, body: bytes) -> tuple[
    Command[_Ping, _Pong, HttpRequest, HttpResponse],
    LoopbackTransport[HttpRequest, HttpResponse],
]:
    def handler(_req: HttpRequest) -> HttpResponse:
        return HttpResponse(status=handler_status, body=body)

    codec: HttpJsonCodec[_Ping, _Pong] = HttpJsonCodec(
        method="POST", path="/ping", request_type=_Ping, response_type=_Pong
    )
    cmd: Command[_Ping, _Pong, HttpRequest, HttpResponse] = Command(
        id="ping",
        codec=codec,
        interpreter=DefaultHttpResponseInterpreter(),
    )
    return cmd, LoopbackTransport(handler)


async def test_dispatch_rejected_on_4xx() -> None:
    cmd, transport = _http_command(404, b"not found")
    async with open_transport(transport):
        result = await dispatch(cmd, transport, _Ping())
    assert result is not None
    assert result.classification is Classification.REJECTED
    assert result.data is None
    assert result.extra == {"http_status": 404}


async def test_dispatch_device_fault_on_5xx() -> None:
    cmd, transport = _http_command(503, b"unavailable")
    async with open_transport(transport):
        result = await dispatch(cmd, transport, _Ping())
    assert result is not None
    assert result.classification is Classification.DEVICE_FAULT
    assert result.extra == {"http_status": 503}


async def test_dispatch_anomalous_on_bad_payload() -> None:
    """Accepted by interpreter (200), but codec.decode raises -> ANOMALOUS."""
    cmd, transport = _http_command(200, b"not-json")
    async with open_transport(transport):
        result = await dispatch(cmd, transport, _Ping())
    assert result is not None
    assert result.classification is Classification.ANOMALOUS
    assert result.extra == {"http_status": 200}


async def test_dispatch_accepted_round_trip() -> None:
    cmd, transport = _http_command(200, b'{"value": 11}')
    async with open_transport(transport):
        result = await dispatch(cmd, transport, _Ping())
    assert result is not None
    assert result.accepted
    assert result.data == _Pong(value=11)


# ---------------------------------------------------------------------------
# Custom interpreter
# ---------------------------------------------------------------------------


class _StrictResponseInterpreter:
    """Reject any response whose body is empty."""

    def classify(self, response: HttpResponse) -> ClassificationVerdict:
        if not response.body:
            return ClassificationVerdict(
                classification=Classification.REJECTED, reason="empty body"
            )
        return ClassificationVerdict(classification=Classification.ACCEPTED)


async def test_custom_interpreter_runs_before_decode() -> None:
    def handler(_req: HttpRequest) -> HttpResponse:
        return HttpResponse(status=200, body=b"")

    codec: HttpJsonCodec[_Ping, _Pong] = HttpJsonCodec(
        method="POST", path="/ping", request_type=_Ping, response_type=_Pong
    )
    cmd: Command[_Ping, _Pong, HttpRequest, HttpResponse] = Command(
        id="ping",
        codec=codec,
        interpreter=_StrictResponseInterpreter(),
    )
    transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(handler)
    async with open_transport(transport):
        result = await dispatch(cmd, transport, _Ping())
    assert result is not None
    assert result.classification is Classification.REJECTED
    assert result.reason == "empty body"


# ---------------------------------------------------------------------------
# Codec contract sanity (Protocol structural check)
# ---------------------------------------------------------------------------


def test_codec_protocol_structural_satisfaction() -> None:
    codec: Codec[_Ping, _Pong, HttpRequest, HttpResponse] = HttpJsonCodec(
        method="POST", path="/ping", request_type=_Ping, response_type=_Pong
    )
    assert codec is not None


def test_codec_error_during_decode_is_classified() -> None:
    """Direct unit check: a CodecError from decode must yield an ANOMALOUS Result."""

    @dataclass(frozen=True)
    class _Req:
        pass

    @dataclass(frozen=True)
    class _Resp:
        pass

    class _BadCodec:
        def encode(self, _: _Req) -> bytes:
            return b""

        def decode(self, _: bytes) -> _Resp:
            raise CodecError("decode boom")

    def handler(payload: bytes) -> bytes:
        return payload

    from kandra_runtime import always_accepted_interpreter

    cmd: Command[_Req, _Resp, bytes, bytes] = Command(
        id="bad", codec=_BadCodec(), interpreter=always_accepted_interpreter
    )
    transport: LoopbackTransport[bytes, bytes] = LoopbackTransport(handler)

    async def _run() -> Result[_Resp] | None:
        async with open_transport(transport):
            return await dispatch(cmd, transport, _Req())

    import asyncio

    result = asyncio.run(_run())
    assert result is not None
    assert result.classification is Classification.ANOMALOUS
    assert "decode boom" in result.reason
