"""Runtime exception hierarchy.

All exceptions raised by the runtime derive from `KandraError`, so client
code can catch one base class to handle any runtime-originated failure.
Standard built-in exceptions (`ValueError`, `TypeError`, etc.) raised by
user-supplied codecs or handlers are left alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection


class KandraError(Exception):
    """Base class for all errors raised by the Kandra runtime."""


class TransportError(KandraError):
    """A transport-layer failure (connection refused, dropped link, etc.)."""


class TransportNotOpenError(TransportError):
    """Raised when a request is attempted before the transport is opened."""


class TransportTimeoutError(TransportError, TimeoutError):
    """A command exceeded its configured timeout.

    Subclasses the standard library `TimeoutError` so code that catches
    the built-in still works.
    """


class CodecError(KandraError):
    """A codec failed to encode a request or decode a response payload."""


class CapabilityUnavailableError(KandraError):
    """Raised when an operation requires capability tags the connected device lacks.

    Only raised after ``discover_capabilities()`` has cached the device's
    capability tags; before discovery, no operation is gated. The offending
    operation id and the still-missing tags are attached for programmatic handling.
    """

    def __init__(self, operation_id: str, missing: Collection[str]) -> None:
        """Record the unavailable operation and the capability tags it still needs."""
        self.operation_id = operation_id
        self.missing: tuple[str, ...] = tuple(missing)
        super().__init__(
            f"operation {operation_id!r} is unavailable on this device "
            f"(missing capabilities: {sorted(self.missing)})"
        )
