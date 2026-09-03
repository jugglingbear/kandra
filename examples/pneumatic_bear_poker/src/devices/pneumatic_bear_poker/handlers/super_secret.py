"""Internal-only bench diagnostics for the Pneumatic Bear Poker.

These helpers are for in-house hardware bring-up and must never ship in a
partner or public SDK, so the example manifest drops the module from the
vendored output via ``vendoring.exclude``.
"""

from __future__ import annotations


def dump_internal_registers() -> dict[str, int]:
    """Return a snapshot of internal actuator registers (bench use only)."""
    return {"piston_position": 0, "valve_state": 1, "fault_flags": 0}
