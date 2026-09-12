"""Payload codecs for the BLE+HTTP fixture device.

``JsonCodec`` backs the HTTP transport; ``FrameCodec`` (length-prefixed JSON over
bytes) backs the BLE transport and exercises the generator's ``BleChannelCodec``
wrapping path. Both satisfy ``kandra_runtime.Codec[Req, Resp, bytes, bytes]``.
"""

from __future__ import annotations

import dataclasses
import json
import struct
from dataclasses import asdict, is_dataclass
from typing import Generic, TypeVar

from kandra_runtime import CodecError

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")

_LENGTH_FMT = "!H"  # big-endian uint16
_HEADER_BYTES = struct.calcsize(_LENGTH_FMT)
_MAX_PAYLOAD = (1 << 16) - 1


class JsonCodec(Generic[RequestT, ResponseT]):
    """Encode/decode a dataclass request/response pair as JSON bytes."""

    def __init__(self, request_type: type[RequestT], response_type: type[ResponseT]) -> None:
        """Bind the codec to a specific request/response type pair."""
        self._request_type = request_type
        self._response_type = response_type

    def encode(self, request: RequestT) -> bytes:
        """Serialize a dataclass request to JSON bytes."""
        return json.dumps(dataclasses.asdict(request)).encode("utf-8")  # type: ignore[call-overload]

    def decode(self, payload: bytes) -> ResponseT:
        """Parse JSON bytes and construct the response dataclass."""
        return self._response_type(**json.loads(payload.decode("utf-8")))


class FrameCodec(Generic[RequestT, ResponseT]):
    """Length-prefixed JSON-over-bytes payload codec for BLE channels."""

    def __init__(self, request_type: type[RequestT], response_type: type[ResponseT]) -> None:
        """Bind to one request/response dataclass pair."""
        self._request_type = request_type
        self._response_type = response_type

    def encode(self, request: RequestT) -> bytes:
        """Serialize a request dataclass to a length-prefixed JSON frame."""
        if not is_dataclass(request) or isinstance(request, type):
            raise CodecError(f"FrameCodec requires a dataclass instance, got {type(request).__name__}")
        body = json.dumps(asdict(request), separators=(",", ":")).encode("utf-8")
        if len(body) > _MAX_PAYLOAD:
            raise CodecError(f"FrameCodec payload too large ({len(body)} > {_MAX_PAYLOAD})")
        return struct.pack(_LENGTH_FMT, len(body)) + body

    def decode(self, payload: bytes) -> ResponseT:
        """Parse a length-prefixed JSON frame into the response dataclass."""
        if len(payload) < _HEADER_BYTES:
            raise CodecError(f"FrameCodec frame too short ({len(payload)} < {_HEADER_BYTES})")
        (declared,) = struct.unpack(_LENGTH_FMT, payload[:_HEADER_BYTES])
        body = payload[_HEADER_BYTES : _HEADER_BYTES + declared]
        if len(body) != declared:
            raise CodecError(f"FrameCodec truncated frame (declared={declared}, got={len(body)})")
        try:
            fields = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CodecError(f"FrameCodec invalid JSON body: {exc}") from exc
        return self._response_type(**fields)
