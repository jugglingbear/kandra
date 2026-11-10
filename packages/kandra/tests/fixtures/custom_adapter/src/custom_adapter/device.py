"""Single-module device wiring a custom transport adapter.

Bundles the handler and a custom transport adapter so the fixture
manifest can reference one module. ``CustomHttpTransport`` is a thin subclass of
the runtime :class:`~kandra_runtime.HttpTransport`; it exists to prove the
manifest ``adapter:`` field is honored (the generated factory calls
``CustomHttpTransport.from_identity(...)`` instead of the runtime default).
"""

from __future__ import annotations

from dataclasses import dataclass

from kandra_runtime import HttpTransport


@dataclass(frozen=True)
class PingRequest:
    """Args for ``widget.ping``."""

    value: int


@dataclass(frozen=True)
class PingResponse:
    """Reply from ``widget.ping``."""

    value: int


class Ping:
    """Echo a value back."""

    request = PingRequest
    response = PingResponse


class CustomHttpTransport(HttpTransport):
    """Custom transport adapter: a thin subclass of the runtime HttpTransport.

    A real project might override ``request`` to add signing, retries, a
    different HTTP client, etc. Here it inherits everything, including the
    ``from_identity`` constructor the generated client calls.
    """
