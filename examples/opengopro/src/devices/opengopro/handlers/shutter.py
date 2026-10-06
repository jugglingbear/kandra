"""Shutter control handlers: start and stop capture.

Open GoPro models the shutter as a single *Set Shutter* command (BLE command
``0x01``) with an on/off parameter. We expose it as two parameterless Kandra
commands -- ``shutter.start`` / ``shutter.stop`` -- so the generated client reads
as ``client.shutter.start()`` / ``client.shutter.stop()``. The differing wire
value is carried as a class-level constant on each request dataclass, so callers
pass no arguments while the BLE codec still has the bytes it needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class StartRequest:
    """``shutter.start`` request -- no caller arguments (Set Shutter, value ON)."""

    command_id: ClassVar[int] = 0x01
    parameter: ClassVar[bytes] = b"\x01"


@dataclass(frozen=True)
class StopRequest:
    """``shutter.stop`` request -- no caller arguments (Set Shutter, value OFF)."""

    command_id: ClassVar[int] = 0x01
    parameter: ClassVar[bytes] = b"\x00"


@dataclass(frozen=True)
class ShutterAck:
    """Empty acknowledgement; success or failure is carried by the ``Result``."""


class Start:
    """Open the shutter (begin recording / photo capture)."""

    request = StartRequest
    response = ShutterAck


class Stop:
    """Close the shutter (stop recording)."""

    request = StopRequest
    response = ShutterAck
