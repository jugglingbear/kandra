"""Runtime exception hierarchy.

All exceptions raised by the runtime derive from `KandraError`, so client
code can catch one base class to handle any runtime-originated failure.
Standard built-in exceptions (`ValueError`, `TypeError`, etc.) raised by
user-supplied codecs or handlers are left alone.
"""

from __future__ import annotations


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
