"""Single-module device wiring a custom transport adapter.

Bundles the handler, codec, and a custom transport adapter so the fixture
manifest can reference one module. ``CustomHttpTransport`` is a thin subclass of
the runtime :class:`~kandra_runtime.HttpTransport`; it exists to prove the
manifest ``adapter:`` field is honored (the generated factory calls
``CustomHttpTransport.from_identity(...)`` instead of the runtime default).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Generic, TypeVar

from kandra_runtime import HttpTransport

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


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


class JsonCodec(Generic[RequestT, ResponseT]):
    """Trivial JSON dataclass codec (declared for the transport; HTTP uses the runtime codec)."""

    def __init__(self, request_type: type[RequestT], response_type: type[ResponseT]) -> None:
        """Bind to a request/response type pair."""
        self._request_type = request_type
        self._response_type = response_type

    def encode(self, request: RequestT) -> bytes:
        """Serialize a dataclass request to JSON bytes."""
        return json.dumps(dataclasses.asdict(request)).encode("utf-8")  # type: ignore[call-overload]

    def decode(self, payload: bytes) -> ResponseT:
        """Parse JSON bytes into the response dataclass."""
        return self._response_type(**json.loads(payload.decode("utf-8")))


class CustomHttpTransport(HttpTransport):
    """Custom transport adapter: a thin subclass of the runtime HttpTransport.

    A real project might override ``request`` to add signing, retries, a
    different HTTP client, etc. Here it inherits everything, including the
    ``from_identity`` constructor the generated client calls.
    """
