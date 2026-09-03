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
    `request()` is the only required I/O primitive today; streaming and
    notification primitives (``subscribe()``, ``stream()``) are deferred
    until the features that consume them (see kandra.md section 8 and
    Q6 in the section-10 decision log).
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
