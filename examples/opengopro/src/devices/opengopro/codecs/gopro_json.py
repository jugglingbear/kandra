"""HTTP codec for Open GoPro's REST shutter endpoints.

The shutter endpoints are parameterless ``GET`` requests
(``/gopro/camera/shutter/start`` and ``/stop``) that return ``{}`` on success,
so the stock ``HttpJsonCodec`` -- which serializes a JSON request body and parses
a typed response -- is overridden to send no body and to map the empty success
response onto the handler's empty ack dataclass.

It is constructed exactly like ``HttpJsonCodec`` (the generated SDK passes
``method`` / ``path`` / request + response types / ``query_from_request``), so it
plugs in as a per-command ``codec:`` override in the manifest.
"""

from __future__ import annotations

from typing import TypeVar

from kandra_runtime import CodecError, HttpJsonCodec, HttpRequest, HttpResponse

ReqT = TypeVar("ReqT")
RespT = TypeVar("RespT")


class GoProJson(HttpJsonCodec[ReqT, RespT]):
    """Send a bare parameterless GET and map the ``{}`` success body to an ack."""

    def encode(self, request: ReqT) -> HttpRequest:
        """Issue the bare GET -- the path encodes the action, so there is no body."""
        return HttpRequest(method=self._method, path=self._path)

    def decode(self, response: HttpResponse) -> RespT:
        """Return the empty ack; the camera replies ``{}`` on success."""
        if self._response_type is None:
            raise CodecError(f"GoPro command at {self._path!r} declared no response type")
        return self._response_type()
