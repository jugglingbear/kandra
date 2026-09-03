"""Safety handler: emergency retract of the bear-poking arm."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmergencyRetractRequest:
    """Args for `safety.emergency_retract`. No-arg request modeled as an empty struct."""


@dataclass(frozen=True)
class EmergencyRetractResponse:
    """Reply from `safety.emergency_retract`."""

    retracted: bool


class EmergencyRetract:
    """Immediately retract the bear-poking arm, regardless of current state."""

    request = EmergencyRetractRequest
    response = EmergencyRetractResponse
