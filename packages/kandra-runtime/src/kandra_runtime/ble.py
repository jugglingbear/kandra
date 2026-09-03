"""BLE transport-family envelope types and built-in channel codec.

Pairs with :class:`~kandra_runtime.ble_transport.BleTransport`. The
envelope carries a *channel name* (resolved by the transport to a
(write_uuid, notify_uuid) pair) plus the raw payload bytes that the
user-supplied payload codec produced.

See kandra.md section 11.9 (one BleTransport per device connection,
named channels declared on the transport, channel selected per command).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic

from kandra_runtime.codec import Codec, RequestT, ResponseT
from kandra_runtime.errors import CodecError


@dataclass(frozen=True)
class BleRequest:
    """Wire envelope consumed by the BLE transport's ``request()``.

    The ``channel`` field selects which (write_uuid, notify_uuid) pair
    the transport routes the payload to. Channels are declared on the
    transport at construction time; the codec just attaches the
    routing name.
    """

    channel: str
    payload: bytes


class BleChannelCodec(Generic[RequestT, ResponseT]):
    """Built-in codec wrapper that adds channel routing to a payload codec.

    BLE has no universal payload format (it's all TLV / protobuf /
    custom). The runtime ships only the channel-routing wrapper; the
    user supplies a payload codec implementing
    :class:`Codec[Req, Resp, bytes, bytes]` that does the actual
    serialization.

    The generator instantiates this wrapper from the manifest's
    per-command ``ble.channel:`` field, plus the transport-level
    ``codec:`` field (the user's payload codec class).

    Args:
        channel: Channel name declared on the BLE transport's
            ``channels`` map.
        payload_codec: User codec that serializes the request dataclass
            to bytes and deserializes response bytes back to the
            response dataclass. Pass ``None`` for void / fire-and-forget
            commands; ``decode`` will raise :class:`CodecError` if
            invoked.
    """

    def __init__(
        self,
        *,
        channel: str,
        payload_codec: Codec[RequestT, ResponseT, bytes, bytes] | None,
    ) -> None:
        """Bind this codec to one channel and one payload codec."""
        if not channel:
            raise ValueError("BleChannelCodec channel must be non-empty")
        self._channel = channel
        self._payload_codec = payload_codec

    def encode(self, request: RequestT) -> BleRequest:
        """Serialize ``request`` to a :class:`BleRequest` envelope."""
        if self._payload_codec is None:
            raise CodecError(
                f"BLE channel {self._channel!r} has no payload codec; cannot encode"
            )
        payload = self._payload_codec.encode(request)
        if not isinstance(payload, bytes):
            raise CodecError(
                f"BLE payload codec returned {type(payload).__name__}, expected bytes"
            )
        return BleRequest(channel=self._channel, payload=payload)

    def decode(self, response: bytes) -> ResponseT:
        """Deserialize response bytes using the inner payload codec."""
        if self._payload_codec is None:
            raise CodecError(
                f"BLE channel {self._channel!r} has no payload codec; cannot decode"
            )
        return self._payload_codec.decode(response)
