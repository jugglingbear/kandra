"""Internal bench tooling: drive the arm past deploy's pressure ceiling.

Granted to the ``internal`` audience only (manifest + ``audience_profiles.yaml``), so a partner SDK never contains it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverdriveRequest:
    """Args for `poker.overdrive`."""

    pressure_psi: int


@dataclass(frozen=True)
class OverdriveResponse:
    """Reply from `poker.overdrive`."""

    delivered_psi: int


class Overdrive:
    """Bench testing only: extend the arm like deploy, but without deploy's 100 psi ceiling."""

    request = OverdriveRequest
    response = OverdriveResponse
