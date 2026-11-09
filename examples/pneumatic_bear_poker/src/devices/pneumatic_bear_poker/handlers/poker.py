"""Operational handlers: extend and retract the pneumatic bear-poking arm."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeployRequest:
    """Args for `poker.deploy`."""

    pressure_psi: int


@dataclass(frozen=True)
class DeployResponse:
    """Reply from `poker.deploy`."""

    delivered_psi: int


class Deploy:
    """Extend the arm at the requested pressure. Rejected while powered off or already extended."""

    request = DeployRequest
    response = DeployResponse


@dataclass(frozen=True)
class RetractRequest:
    """Args for `poker.retract`. No-arg request modeled as an empty struct."""


@dataclass(frozen=True)
class RetractResponse:
    """Reply from `poker.retract`."""

    retracted: bool


class Retract:
    """Pull the arm back. A no-op when already retracted; rejected while powered off."""

    request = RetractRequest
    response = RetractResponse
