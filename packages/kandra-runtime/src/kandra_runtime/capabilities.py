"""Capability negotiation: ask a device what it supports before calling in.

An operation (command / attribute / event) may declare required capability
*tags* in the manifest (``capabilities: [...]``). A :class:`CapabilityProbe` — a
device-specific adapter you implement, like a codec or transport adapter —
reports which tags the *connected* device supports. The generated client's
``discover_capabilities(probe)`` caches that set; afterwards, calling an
operation whose required tags the device lacks raises
:class:`~kandra_runtime.errors.CapabilityUnavailable` locally instead of failing
on the wire.

Kandra never imposes a capabilities format on the device: tags are opaque
strings you choose, matched by set-containment. Gate at whatever granularity you
need — a shared feature tag for operations that ship together, or a per-operation
tag (commonly the op id) when a device may support one operation of a feature but
not another.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Collection


@dataclass(frozen=True)
class Capabilities:
    """The capability tags a connected device reported (see :class:`CapabilityProbe`)."""

    supported: frozenset[str]

    def supports(self, required: Collection[str]) -> bool:
        """Return ``True`` iff every tag in ``required`` is supported by the device."""
        return set(required) <= self.supported


@runtime_checkable
class CapabilityProbe(Protocol):
    """Reports which capability tags a connected device supports.

    A device-specific adapter you implement — Kandra defines the protocol, you
    supply the behavior (a hardcoded table from a published spec, a live
    capabilities endpoint queried through the passed client, ...). Return the set
    of tags the device supports; the client gates operations by set-containment.
    """

    async def probe(self, client: Any) -> frozenset[str]:
        """Query ``client``'s device and return its supported capability tag set."""
        ...


class StaticCapabilityProbe:
    """A :class:`CapabilityProbe` returning a fixed tag set.

    For tests and devices whose capabilities are known statically (e.g. hardcoded
    from a published spec rather than queried at runtime)::

        caps = await client.discover_capabilities(StaticCapabilityProbe("gps", "hdr"))
    """

    def __init__(self, *tags: str) -> None:
        """Capture the fixed set of supported capability tags."""
        self._tags = frozenset(tags)

    async def probe(self, client: Any) -> frozenset[str]:
        """Return the fixed tag set, ignoring ``client``."""
        return self._tags
