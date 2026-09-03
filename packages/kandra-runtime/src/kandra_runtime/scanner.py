"""Device discovery primitives: :class:`Candidate`, :class:`Scanner`, and helpers.

A *candidate* is an in-range device that *might* be the one the caller
wants — it has just enough metadata for a user-supplied
:class:`Matcher` to accept or reject it. Once accepted, the candidate
is handed to an :class:`~kandra_runtime.enrollment.Enrollment` adapter
to produce a persistent :class:`~kandra_runtime.identity.Identity`.

Two entry points are exposed:

* :meth:`Scanner.scan` — the streaming primitive. Yields candidates as
  they are observed until the iterator is closed by the caller (or the
  optional ``timeout`` elapses). Best for "connect to the first match"
  flows and live UIs that want to render devices as they appear.
* :func:`snapshot_scan` — thin collect-for-N-seconds helper that
  consumes :meth:`Scanner.scan` and returns a list. Best for "show me
  the menu, let me pick" CLI flows.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Candidate:
    """A device observed during a scan.

    The fields are intentionally minimal; transport-specific metadata
    (BLE service UUIDs, mDNS TXT records, signal strength, …) lives in
    ``metadata`` as a free-form ``Mapping[str, Any]``. :class:`Matcher`
    implementations consult those fields to decide acceptance.
    """

    transport: str
    """Transport family that discovered the candidate (``"ble"``, ``"http"``, …)."""

    address: str
    """Stable per-transport address (BLE MAC, base URL, mDNS instance, …)."""

    advertised_name: str | None = None
    """Friendly name as advertised by the device, when available."""

    metadata: Mapping[str, Any] = ()  # type: ignore[assignment]
    """Transport-specific metadata (service UUIDs, TXT records, RSSI, …)."""

    def __post_init__(self) -> None:
        """Coerce a missing metadata default (empty tuple) into an empty mapping."""
        # Allow metadata to be omitted by treating empty tuple as empty dict.
        if not isinstance(self.metadata, Mapping):
            object.__setattr__(self, "metadata", {})


Matcher = Callable[[Candidate], bool]
"""Predicate selecting which candidates are worth surfacing to the caller."""


def accept_all(_candidate: Candidate) -> bool:
    """Default :class:`Matcher` — surfaces every observed candidate."""
    return True


@runtime_checkable
class Scanner(Protocol):
    """Discovers in-range devices and surfaces them as :class:`Candidate` records.

    Implementations are expected to be reusable across multiple
    ``scan()`` invocations but need not be safe to call concurrently
    against themselves.
    """

    def scan(
        self,
        *,
        matcher: Matcher = accept_all,
        timeout: float | None = None,
    ) -> AsyncIterator[Candidate]:
        """Yield candidates as they are observed.

        Parameters
        ----------
        matcher:
            Predicate applied per candidate; only matches are yielded.
        timeout:
            Optional wall-clock budget in seconds. When ``None``, the
            scan runs until the caller closes the iterator.
        """


async def snapshot_scan(
    scanner: Scanner,
    *,
    timeout: float,
    matcher: Matcher = accept_all,
) -> list[Candidate]:
    """Collect every candidate observed by ``scanner`` for ``timeout`` seconds.

    Returns the collected list in observation order, **deduplicated by
    ``(transport, address)``** — a single device that re-advertises
    during the window is reported once with the first observed metadata.
    """
    if timeout <= 0:
        raise ValueError(f"snapshot_scan timeout must be > 0, got {timeout!r}")

    seen: dict[tuple[str, str], Candidate] = {}
    iterator = scanner.scan(matcher=matcher, timeout=timeout)
    try:
        async with asyncio.timeout(timeout):
            async for candidate in iterator:
                key = (candidate.transport, candidate.address)
                if key not in seen:
                    seen[key] = candidate
    except TimeoutError:
        pass
    finally:
        # Close the iterator promptly so transport resources are released.
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()
    return list(seen.values())
