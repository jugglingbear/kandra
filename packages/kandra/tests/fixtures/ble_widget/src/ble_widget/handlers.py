"""Command handler for the BLE+HTTP fixture device."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PingRequest:
    """Args for ``widget.ping``."""

    value: int


@dataclass(frozen=True)
class PingResponse:
    """Reply from ``widget.ping``."""

    value: int


class Ping:
    """Echo a value back -- a trivial command wired over both transports."""

    request = PingRequest
    response = PingResponse
