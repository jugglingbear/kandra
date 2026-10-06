"""Human-readable, transport-agnostic rendering of a wire request envelope.

Used for lightweight wire tracing: :func:`~kandra_runtime.command.dispatch` emits
the formatted envelope on the ``kandra.wire.<command-id>`` logger at ``DEBUG``
right after ``codec.encode``, so any consumer can see the exact URL / bytes a
command puts on the wire regardless of transport family.
"""

from __future__ import annotations

from kandra_runtime.ble import BleRequest
from kandra_runtime.http import HttpRequest

# Cap long bodies/payloads so a trace line stays one readable line.
_MAX_PREVIEW = 256


def format_wire(envelope: object) -> str:
    """Render a wire request envelope to a one-line human-readable string.

    Recognises the built-in HTTP and BLE envelopes and falls back to a hex
    preview for raw ``bytes`` (e.g. loopback) or ``repr`` for anything else, so
    it works for every transport family without the caller knowing which.
    """
    if isinstance(envelope, HttpRequest):
        line = f"{envelope.method} {envelope.path}{_query(envelope.query)}"
        if envelope.body:
            line += f"  {_body(envelope.body)}"
        return line
    if isinstance(envelope, BleRequest):
        return f"ble[{envelope.channel}] {_hex(envelope.payload)} ({len(envelope.payload)} bytes)"
    if isinstance(envelope, bytes | bytearray):
        raw = bytes(envelope)
        return f"{_hex(raw)} ({len(raw)} bytes)"
    return repr(envelope)


def _query(query: dict[str, str]) -> str:
    if not query:
        return ""
    return "?" + "&".join(f"{key}={value}" for key, value in query.items())


def _body(body: bytes) -> str:
    """Decoded text when the body is valid UTF-8, else a hex preview."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return f"{_hex(body)} ({len(body)} bytes)"
    if len(text) > _MAX_PREVIEW:
        return f"{text[:_MAX_PREVIEW]}... ({len(body)} bytes)"
    return text


def _hex(data: bytes) -> str:
    preview = data[: _MAX_PREVIEW // 2]
    return preview.hex() + "..." if len(data) > len(preview) else preview.hex()
