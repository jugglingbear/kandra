"""Command wiring and dispatch.

A `Command` binds a manifest id, a codec, a interpreter, and per-command
behavior flags together. Dispatch is a free function that runs
``codec.encode -> transport.request -> interpreter.classify`` and (when
the verdict is :attr:`Classification.ACCEPTED`) ``codec.decode``,
wrapping everything into a :class:`Result` envelope.

When ``expects_response=False`` the command is fire-and-forget: dispatch
sends the request, swallows a `TransportTimeoutError`, skips classify
and decode entirely, and returns ``None``. This is how
firmware-tear-down commands (factory reset, wireless reset) are
modelled. See kandra.md sections 11.3 / 11.5.

The runtime exposes both async (`dispatch`) and sync (`dispatch_sync`)
entry points. Transport lifecycle (`open` / `close`) is the caller's
responsibility -- usually via `open_transport()` from the transport
module, or via the generated client's async context manager.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, cast

from kandra_runtime.codec import RequestT, ResponseT, WireReqT, WireRespT
from kandra_runtime.errors import CodecError, IdentityStaleError, TransportError, TransportTimeoutError
from kandra_runtime.result import Classification, ResponseInterpreter, Result
from kandra_runtime.transport import Subscribable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from kandra_runtime.codec import Codec
    from kandra_runtime.transport import Transport


@dataclass(frozen=True)
class Command(Generic[RequestT, ResponseT, WireReqT, WireRespT]):
    """A typed command binding for a single (command, transport) pair.

    Attributes:
        id: Dotted command identifier from the manifest (e.g. `media.list_files`).
        codec: Encodes the request and decodes the response.
        interpreter: Pluggable rule mapping the wire response to a
            :class:`Classification`. The generator wires the
            transport-family default (e.g. :func:`default_http_interpreter`)
            when no override is supplied.
        timeout: Optional per-call timeout in seconds. `None` disables the
            timeout (the transport may still impose its own).
        expects_response: When False, the command is fire-and-forget.
            Dispatch treats a timeout as success and returns ``None``
            without invoking the interpreter or ``codec.decode``.
        idempotent: When True, the command is safe to auto-resend, so
            :func:`dispatch` may retry it after a transient transport
            failure. Non-idempotent commands are never retried.
        retries: Extra attempts after a transient transport failure.
            Only honored when ``idempotent`` is True; ignored otherwise.
    """

    id: str
    codec: Codec[RequestT, ResponseT, WireReqT, WireRespT]
    interpreter: ResponseInterpreter[WireRespT]
    timeout: float | None = None
    expects_response: bool = True
    idempotent: bool = False
    retries: int = 0


async def dispatch(
    command: Command[RequestT, ResponseT, WireReqT, WireRespT],
    transport: Transport[WireReqT, WireRespT],
    request: RequestT,
) -> Result[ResponseT] | None:
    """Async-dispatch a command over an already-open transport.

    Returns a :class:`Result` envelope, or ``None`` when
    ``command.expects_response`` is False.

    The result is always returned -- non-ACCEPTED classifications are
    *not* raised. The generated client decides whether to invoke a
    framework-installed ``on_non_accepted`` hook; this function is the
    raw dispatch primitive.
    """
    envelope = command.codec.encode(request)
    if not command.expects_response:
        with contextlib.suppress(TransportTimeoutError):
            await _request_with_timeout(transport, envelope, command)
        return None

    try:
        response = await _request_with_retry(transport, envelope, command)
    except IdentityStaleError:
        # Recoverable + distinct: let the client's connect / mid-session recovery
        # refresh credentials rather than flattening it to a TRANSPORT_FAILURE.
        raise
    except TransportError as exc:
        return Result(
            classification=Classification.TRANSPORT_FAILURE,
            reason=str(exc) or type(exc).__name__,
        )

    verdict = command.interpreter.classify(response)
    if verdict.classification is not Classification.ACCEPTED:
        return Result(
            classification=verdict.classification,
            reason=verdict.reason,
            extra=verdict.extra,
        )
    try:
        payload = command.codec.decode(response)
    except CodecError as exc:
        return Result(
            classification=Classification.ANOMALOUS,
            reason=str(exc),
            extra=verdict.extra,
        )
    return Result(
        classification=Classification.ACCEPTED,
        data=payload,
        extra=verdict.extra,
    )


def dispatch_sync(
    command: Command[RequestT, ResponseT, WireReqT, WireRespT],
    transport: Transport[WireReqT, WireRespT],
    request: RequestT,
) -> Result[ResponseT] | None:
    """Sync wrapper around `dispatch`.

    Spins up a fresh event loop for the call via `asyncio.run`, so this
    must not be called from inside a running loop. The caller is
    responsible for opening and closing the transport.
    """
    return asyncio.run(dispatch(command, transport, request))


async def dispatch_subscribe(
    command: Command[RequestT, ResponseT, WireReqT, WireRespT],
    transport: Transport[WireReqT, WireRespT],
    request: RequestT,
) -> AsyncIterator[Result[ResponseT]]:
    """Subscribe over a transport and yield a :class:`Result` per pushed update.

    Reuses the command's codec + interpreter: ``codec.encode(request)`` builds
    the subscription envelope, then each device-pushed wire response is
    classified and (when :attr:`Classification.ACCEPTED`) decoded, yielding one
    ``Result[ResponseT]`` per event until the returned iterator is closed.

    The transport must implement :class:`~kandra_runtime.transport.Subscribable`
    (native push — BLE Notify, HTTP SSE). The opt-in polling fallback is a
    higher-level wrapper (repeated :func:`dispatch`), never this primitive. A
    mid-stream transport failure ends the stream with a final
    ``TRANSPORT_FAILURE`` result rather than raising.

    Raises:
        TransportError: the wired transport does not support subscription.
    """
    if not isinstance(transport, Subscribable):
        raise TransportError(f"transport {type(transport).__name__} does not support subscribe()")
    subscribable = cast("Subscribable[WireReqT, WireRespT]", transport)
    envelope = command.codec.encode(request)
    try:
        async for wire in subscribable.subscribe(envelope):
            yield _classify_and_decode(command, wire)
    except TransportError as exc:
        yield Result(
            classification=Classification.TRANSPORT_FAILURE,
            reason=str(exc) or type(exc).__name__,
        )


def _classify_and_decode(
    command: Command[RequestT, ResponseT, WireReqT, WireRespT],
    wire: WireRespT,
) -> Result[ResponseT]:
    """Classify + (when accepted) decode a single wire response into a Result."""
    verdict = command.interpreter.classify(wire)
    if verdict.classification is not Classification.ACCEPTED:
        return Result(classification=verdict.classification, reason=verdict.reason, extra=verdict.extra)
    try:
        payload = command.codec.decode(wire)
    except CodecError as exc:
        return Result(classification=Classification.ANOMALOUS, reason=str(exc), extra=verdict.extra)
    return Result(classification=Classification.ACCEPTED, data=payload, extra=verdict.extra)


async def _request_with_retry(
    transport: Transport[WireReqT, WireRespT],
    envelope: WireReqT,
    command: Command[RequestT, ResponseT, WireReqT, WireRespT],
) -> WireRespT:
    """Send a request, retrying transient transport failures for idempotent commands.

    Retries up to ``command.retries`` extra times when ``command.idempotent`` is
    set; a non-idempotent command gets a single attempt (re-sending it could
    double-execute). ``IdentityStaleError`` is never retried -- it propagates so
    the client's connect / mid-session recovery can refresh credentials.
    """
    max_attempts = 1 + (command.retries if command.idempotent else 0)
    attempt = 0
    while True:
        attempt += 1
        try:
            return await _request_with_timeout(transport, envelope, command)
        except IdentityStaleError:
            raise
        except TransportError:
            if attempt >= max_attempts:
                raise


async def _request_with_timeout(
    transport: Transport[WireReqT, WireRespT],
    envelope: WireReqT,
    command: Command[RequestT, ResponseT, WireReqT, WireRespT],
) -> WireRespT:
    if command.timeout is None:
        return await transport.request(envelope)
    try:
        return await asyncio.wait_for(transport.request(envelope), command.timeout)
    except TimeoutError as exc:
        raise TransportTimeoutError(
            f"command {command.id!r} timed out after {command.timeout}s"
        ) from exc


# Silence "unused import" warnings on TYPE_CHECKING-only names used in docs.
_ = Any
