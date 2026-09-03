"""Power-rail control: enable / disable the pneumatic compressor."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PowerOnRequest:
    """Args for `power.on`. Empty -- powering on takes no parameters."""


@dataclass(frozen=True)
class PowerOnResponse:
    """Reply from `power.on`."""

    powered: bool


@dataclass(frozen=True)
class PowerOffRequest:
    """Args for `power.off`."""

    drain_pressure: bool = True
    """If True, vent residual line pressure before cutting power."""


@dataclass(frozen=True)
class PowerOffResponse:
    """Reply from `power.off`."""

    powered: bool
    residual_psi: int


class PowerOn:
    """Energise the compressor and bring the air rail up to standby pressure."""

    request = PowerOnRequest
    response = PowerOnResponse


class PowerOff:
    """De-energise the compressor; optionally vent residual pressure first."""

    request = PowerOffRequest
    response = PowerOffResponse
