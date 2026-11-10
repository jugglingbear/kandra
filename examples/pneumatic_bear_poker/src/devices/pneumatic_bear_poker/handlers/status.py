"""Read-only device attribute: what the device is doing right now."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Status:
    """Power, arm position, and the pressure the arm is holding (0 when retracted)."""

    powered: bool
    extended: bool
    psi: int


class DeviceStatus:
    """`device.status` -- read what the device is doing right now."""

    value = Status
