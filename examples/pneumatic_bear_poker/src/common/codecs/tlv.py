"""Minimal length-prefixed JSON-over-bytes codec for the Bear Poker BLE link.

Frames are ``[uint16-big-endian length][JSON-encoded payload]``.

This is intentionally simple — real devices use TLV / protobuf / their
own framing. For the example we want a credible codec that:

* satisfies :class:`kandra_runtime.Codec[Req, Resp, bytes, bytes]`,
* exercises the generator's BLE wiring path end-to-end, and
* shows how a user-supplied payload codec composes with the runtime's
  :class:`kandra_runtime.BleChannelCodec` channel-routing wrapper.
"""

from __future__ import annotations

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


class LengthPrefixedTLV(Generic[RequestT, ResponseT]):
    """Length-prefixed JSON-over-bytes payload codec for BLE channels."""

    def __init__(self, request_type: type[RequestT], response_type: type[ResponseT]) -> None:
        """Bind to one request/response dataclass pair."""
        self._request_type = request_type
        self._response_type = response_type

    def encode(self, request: RequestT) -> bytes:
        """Serialize a request dataclass to a length-prefixed JSON frame."""
        if not is_dataclass(request) or isinstance(request, type):
            raise CodecError(
                f"LengthPrefixedTLV requires a dataclass instance, got {type(request).__name__}"
            )
        body = json.dumps(asdict(request), separators=(",", ":")).encode("utf-8")
        if len(body) > _MAX_PAYLOAD:
            raise CodecError(
                f"LengthPrefixedTLV payload too large ({len(body)} > {_MAX_PAYLOAD})"
            )
        return struct.pack(_LENGTH_FMT, len(body)) + body

    def decode(self, payload: bytes) -> ResponseT:
        """Parse a length-prefixed JSON frame into the response dataclass."""
        if len(payload) < _HEADER_BYTES:
            raise CodecError(
                f"LengthPrefixedTLV frame too short ({len(payload)} < {_HEADER_BYTES})"
            )
        (declared,) = struct.unpack(_LENGTH_FMT, payload[:_HEADER_BYTES])
        body = payload[_HEADER_BYTES : _HEADER_BYTES + declared]
        if len(body) != declared:
            raise CodecError(
                f"LengthPrefixedTLV truncated frame (declared={declared}, got={len(body)})"
            )
        try:
            fields = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CodecError(f"LengthPrefixedTLV invalid JSON body: {exc}") from exc
        try:
            return self._response_type(**fields)
        except TypeError as exc:
            raise CodecError(
                f"LengthPrefixedTLV cannot construct {self._response_type.__name__}: {exc}"
            ) from exc
