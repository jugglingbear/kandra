"""In-memory loopback transport for tests and documentation examples.

Wraps a user-supplied handler that maps a request envelope to a response
envelope. Both sync and async handlers are supported.

Generic on the wire envelope types so it satisfies any
``Transport[WireReqT, WireRespT]`` slot -- ``Transport[bytes, bytes]``
for raw payload tests, ``Transport[HttpRequest, HttpResponse]`` for HTTP
codec round-trip tests, etc.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Generic, TypeVar

from kandra_runtime.errors import TransportError, TransportNotOpenError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

WireReqT = TypeVar("WireReqT")
WireRespT = TypeVar("WireRespT")


class LoopbackTransport(Generic[WireReqT, WireRespT]):
    """A transport that routes requests through an in-process handler.

    Useful for unit tests, runtime self-checks, and as the smallest
    possible reference implementation of the `Transport` protocol.

    Pass ``subscribe_handler`` to also satisfy the
    :class:`~kandra_runtime.transport.Subscribable` protocol: it maps a request
    envelope to an ``AsyncIterator`` of wire responses (a scripted device push
    stream), which the attribute / event layers consume.
    """

    def __init__(
        self,
        handler: Callable[[WireReqT], WireRespT | Awaitable[WireRespT]],
        *,
        subscribe_handler: Callable[[WireReqT], AsyncIterator[WireRespT]] | None = None,
    ) -> None:
        """Build a loopback transport bound to a request handler."""
        self._handler = handler
        self._subscribe_handler = subscribe_handler
        self._open = False

    async def open(self) -> None:
        """Mark the transport as ready to accept requests."""
        self._open = True

    async def close(self) -> None:
        """Mark the transport as closed; subsequent requests will raise."""
        self._open = False

    @property
    def is_open(self) -> bool:
        """True between ``open()`` returning and ``close()`` being called."""
        return self._open

    async def request(self, envelope: WireReqT) -> WireRespT:
        """Run the handler against `envelope` and return its result.

        Awaits the result if the handler is a coroutine function or
        otherwise returns an awaitable.
        """
        if not self._open:
            raise TransportNotOpenError("LoopbackTransport is not open")
        result = self._handler(envelope)
        if inspect.isawaitable(result):
            return await result
        return result

    def subscribe(self, envelope: WireReqT) -> AsyncIterator[WireRespT]:
        """Yield the scripted subscribe stream for `envelope` until it is closed.

        Raises:
            TransportError: constructed without a ``subscribe_handler``.
            TransportNotOpenError: called before :meth:`open` (raised when
                iteration begins).
        """
        handler = self._subscribe_handler
        if handler is None:
            raise TransportError("LoopbackTransport was constructed without a subscribe_handler")

        async def _stream() -> AsyncIterator[WireRespT]:
            if not self._open:
                raise TransportNotOpenError("LoopbackTransport is not open")
            async for item in handler(envelope):
                yield item

        return _stream()
