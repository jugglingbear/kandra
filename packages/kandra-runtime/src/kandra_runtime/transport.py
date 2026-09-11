"""Transport abstraction.

A transport ferries a typed wire envelope between the client and the
device. It is deliberately ignorant of message *semantics* -- semantics
live in the `Codec` layer. The envelope types are transport-family
specific:

* HTTP transport: ``Transport[HttpRequest, HttpResponse]``
* BLE transport:  ``Transport[BleRequest, bytes]``
* Loopback:       ``Transport[bytes, bytes]``

The protocol is async-first; sync callers go through the generated
``SyncClient`` wrapper or the ``dispatch_sync`` helper.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

WireReqT = TypeVar("WireReqT")
WireRespT = TypeVar("WireRespT")

_WireReqT_contra = TypeVar("_WireReqT_contra", contravariant=True)
_WireRespT_co = TypeVar("_WireRespT_co", covariant=True)


@runtime_checkable
class Transport(Protocol[_WireReqT_contra, _WireRespT_co]):
    """A bidirectional channel to a device, typed on its wire envelope.

    Implementations must be safe to `open()` and `close()` more than once.
    `request()` is the only required I/O primitive. Subscription (device
    push) lives in the separate :class:`Subscribable` protocol so a transport
    only advertises it when it can honour it; byte-stream downloads
    (``stream()``) remain deferred until a consumer lands.
    """

    async def open(self) -> None:
        """Establish the underlying connection."""
        ...

    async def close(self) -> None:
        """Tear down the underlying connection."""
        ...

    @property
    def is_open(self) -> bool:
        """True between `open()` returning and `close()` being called."""
        ...

    async def request(self, envelope: _WireReqT_contra) -> _WireRespT_co:
        """Send a request envelope and return the response envelope."""
        ...


@runtime_checkable
class Subscribable(Protocol[_WireReqT_contra, _WireRespT_co]):
    """A transport that can stream device-pushed responses for a subscription.

    Consumed by attribute-`subscribe` and events (kandra.md section 3.4.1 / 7).
    This is **native push only** — BLE Notify, HTTP SSE, etc. The explicit
    opt-in HTTP *polling* fallback is a higher-level wrapper (repeated
    ``request()`` on a timer), never a transport primitive, so polling never
    turns on by surprise.

    Not every transport is subscribable; attribute / event dispatch checks for
    this protocol and raises a clear error when a wired transport lacks it.
    """

    def subscribe(self, envelope: _WireReqT_contra) -> AsyncIterator[_WireRespT_co]:
        """Open a subscription; yield wire responses until the iterator is closed.

        Closing the returned async iterator (breaking the ``async for`` or
        calling ``aclose()``) must tear down the underlying subscription
        (unsubscribe / close the stream).
        """
        ...


_T = TypeVar("_T", bound=Transport)  # type: ignore[type-arg]


@asynccontextmanager
async def open_transport(transport: _T) -> AsyncIterator[_T]:
    """Async context manager that opens a transport for the duration of the block.

    Usage::

        async with open_transport(transport) as t:
            response = await dispatch(command, t, request)
    """
    await transport.open()
    try:
        yield transport
    finally:
        await transport.close()
