"""The one and only handler: PUT a greeting, get a reply back.

A Kandra "handler" is just a class that names a request type and a response type.
The generator reads those two attributes to emit a fully typed client method --
here, ``client.greeting.hello(HelloRequest) -> HelloResponse``. There is no
client-side logic to write: the device does the work, the codec moves the bytes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelloRequest:
    """What you send. Serialized to JSON as the PUT body, e.g. ``{"message": "hello"}``."""

    message: str


@dataclass(frozen=True)
class HelloResponse:
    """What the device sends back, e.g. ``{"reply": "world"}``."""

    reply: str


class Hello:
    """Bind the request/response pair to the ``greeting.hello`` command."""

    request = HelloRequest
    response = HelloResponse
