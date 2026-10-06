"""BLE payload codec for the Open GoPro Control & Query command channel.

Open GoPro BLE commands use a length-prefixed TLV framing on the command write
characteristic::

    [total-length][command-id][parameter-length][parameter-bytes...]

and the camera answers on the notify characteristic with::

    [total-length][command-id][status]

where ``status == 0x00`` means success. This codec assembles the request framing
from each command's ``command_id`` / ``parameter`` and validates the response
status byte. It implements ``Codec[Req, Resp, bytes, bytes]``; the generated SDK
wraps it in the runtime ``BleChannelCodec`` to attach channel routing.
"""

from __future__ import annotations

from typing import ClassVar, Generic, Protocol, TypeVar

from kandra_runtime import CodecError


class GoProCommand(Protocol):
    """Structural type for a request carrying Open GoPro command TLV fields."""

    command_id: ClassVar[int]
    parameter: ClassVar[bytes]


ReqT = TypeVar("ReqT", bound=GoProCommand)
RespT = TypeVar("RespT")


class GoProTlv(Generic[ReqT, RespT]):
    """Assemble and parse the Open GoPro BLE command TLV for one command."""

    def __init__(self, request_type: type[ReqT], response_type: type[RespT]) -> None:
        """Bind the codec to a command's request/response dataclasses."""
        self._request_type = request_type
        self._response_type = response_type

    def encode(self, request: ReqT) -> bytes:
        """Frame ``request`` as ``[len][command-id][param-len][parameter]``."""
        body = bytes([request.command_id, len(request.parameter)]) + request.parameter
        return bytes([len(body)]) + body

    def decode(self, response: bytes) -> RespT:
        """Validate the response TLV's status byte and return the ack."""
        if len(response) < 3:
            raise CodecError(f"GoPro TLV response too short: {response!r}")
        status = response[2]
        if status != 0x00:
            raise CodecError(f"GoPro command {response[1]:#04x} failed (status {status:#04x})")
        return self._response_type()
