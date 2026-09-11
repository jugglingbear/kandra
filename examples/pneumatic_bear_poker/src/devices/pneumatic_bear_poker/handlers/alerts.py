"""Stateless device event: the bear stirred (a subscribe-only emission).

Unlike a command (a one-shot action) or an attribute (readable/writable state),
an *event* is a fire-and-forget emission — there is no stored value, only a
stream to subscribe to. The handler declares a single ``payload`` type
describing each emission.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BearStirred:
    """One "the bear stirred" emission."""

    magnitude: int  # how much the bear moved, on a 0–100 scale


class BearStirredAlert:
    """`alerts.bear_stirred` — emitted whenever the bear shifts in its sleep."""

    payload = BearStirred
