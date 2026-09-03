"""Trivial JSON codec backed by dataclasses + the stdlib `json` module."""

from __future__ import annotations

import dataclasses
import json
from typing import Generic, TypeVar

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class JsonCodec(Generic[RequestT, ResponseT]):
    """Encode/decode a dataclass request/response pair as JSON bytes.

    Both `request_type` and `response_type` must be frozen dataclasses with
    JSON-serializable fields.
    """

    def __init__(self, request_type: type[RequestT], response_type: type[ResponseT]) -> None:
        """Bind the codec to a specific request/response type pair."""
        self._request_type = request_type
        self._response_type = response_type

    def encode(self, request: RequestT) -> bytes:
        """Serialize a dataclass request to JSON bytes."""
        return json.dumps(dataclasses.asdict(request)).encode("utf-8")

    def decode(self, payload: bytes) -> ResponseT:
        """Parse JSON bytes and construct the response dataclass."""
        data = json.loads(payload.decode("utf-8"))
        return self._response_type(**data)
