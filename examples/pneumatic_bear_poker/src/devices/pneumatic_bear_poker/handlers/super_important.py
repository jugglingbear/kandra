"""Runtime-loaded calibration helpers for the Pneumatic Bear Poker.

This module is pulled in dynamically (by name) at runtime rather than through a
static ``import``, so the generator's import-closure walker cannot discover it.
The example manifest therefore force-vendors it via ``vendoring.extra_include``.
"""

from __future__ import annotations


def poke_calibration_factor(pressure_psi: int) -> float:
    """Map a requested pressure to the device's calibrated actuation factor."""
    return round(1.0 + pressure_psi / 100.0, 3)
