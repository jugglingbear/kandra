"""Settable device attribute: the pneumatic poke-intensity level.

Unlike a command (a one-shot action), an *attribute* is a named piece of
device state you can ``read``, ``write``, and ``subscribe`` to. The handler
declares a single ``value`` type — the payload returned by reads and pushed by
subscriptions, and the input accepted by writes. This device echoes the new
value on write, so there is no separate ``write_ack`` type.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PokeIntensity:
    """How hard the arm pokes, on a 0–11 scale."""

    level: int


class PokeIntensitySetting:
    """`settings.poke_intensity` — read / write / subscribe the poke level."""

    value = PokeIntensity
