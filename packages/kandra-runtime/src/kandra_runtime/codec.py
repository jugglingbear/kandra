"""Codec abstraction: object <-> wire envelope for a single command.

A codec maps between the user-facing typed request/response and the
transport-family-specific wire envelopes (e.g. `HttpRequest` for HTTP,
`bytes` for loopback, `BleRequest` for BLE).

The four type parameters are:

* ``RequestT``  -- the user's request dataclass (e.g. ``DeployRequest``).
* ``ResponseT`` -- the user's response dataclass (e.g. ``DeployResponse``).
* ``WireReqT``  -- the envelope the transport's ``request()`` consumes.
* ``WireRespT`` -- the envelope the transport's ``request()`` produces.

End users rarely write this raw four-param signature. They subclass a
**family-paired** base shipped with each built-in transport -- for
example ``HttpJsonCodec[Req, Resp]`` (``WireReqT`` / ``WireRespT``
already fixed to ``HttpRequest`` / ``HttpResponse``). See kandra.md
section 11.5 and the per-family modules (``kandra_runtime.http``, etc.).
"""

from __future__ import annotations

from typing import Protocol, TypeVar

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")
WireReqT = TypeVar("WireReqT")
WireRespT = TypeVar("WireRespT")

_RequestT_contra = TypeVar("_RequestT_contra", contravariant=True)
_ResponseT_co = TypeVar("_ResponseT_co", covariant=True)
_WireReqT_co = TypeVar("_WireReqT_co", covariant=True)
_WireRespT_contra = TypeVar("_WireRespT_contra", contravariant=True)


class Codec(Protocol[_RequestT_contra, _ResponseT_co, _WireReqT_co, _WireRespT_contra]):
    """Serializes a command's request/response pair to/from a wire envelope.

    Implementations should raise `kandra_runtime.errors.CodecError`
    (or a subclass) on encode/decode failures so callers can catch
    a single base class.
    """

    def encode(self, request: _RequestT_contra) -> _WireReqT_co:
        """Serialize a request object to a transport wire envelope."""
        ...

    def decode(self, response: _WireRespT_contra) -> _ResponseT_co:
        """Deserialize a transport wire envelope to a response object."""
        ...
