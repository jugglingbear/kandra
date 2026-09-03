"""Operational handler: deploy the pneumatic bear-poking arm."""

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
    """Deploy the bear-poking arm at the requested pressure."""

    request = DeployRequest
    response = DeployResponse
