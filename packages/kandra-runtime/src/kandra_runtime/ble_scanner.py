"""BLE :class:`Scanner` adapter built on top of :mod:`bleak`.

Uses :class:`bleak.BleakScanner` in advertisement-detection-callback
mode so :meth:`scan` can yield candidates as soon as the first
advertisement for each device arrives, rather than waiting for the
whole scan window to elapse.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol, cast, runtime_checkable

from kandra_runtime.errors import TransportError
from kandra_runtime.scanner import Candidate, Matcher, Scanner, accept_all


@runtime_checkable
class _BleakScannerLike(Protocol):
    """Minimal subset of :class:`bleak.BleakScanner` we depend on."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


_ScannerFactory = Callable[
    # (detection_callback,) -> scanner
    [Callable[[Any, Any], None]],
    _BleakScannerLike,
]


def _default_scanner_factory(detection_callback: Callable[[Any, Any], None]) -> _BleakScannerLike:
    from bleak import BleakScanner  # local import keeps test envs without BLE happy

    return cast(_BleakScannerLike, BleakScanner(detection_callback=detection_callback))


class BleScanner(Scanner):
    """BLE adapter implementing :class:`~kandra_runtime.scanner.Scanner`.

    Each :meth:`scan` call starts a fresh :class:`bleak.BleakScanner`,
    streams candidates until the iterator is closed or ``timeout``
    elapses, then stops the scanner. Concurrent calls to :meth:`scan`
    on the same instance are not supported (bleak's scanner is a
    singleton resource per process on most platforms).
    """

    def __init__(self, *, scanner_factory: _ScannerFactory | None = None) -> None:
        """Create a BLE scanner adapter.

        Parameters
        ----------
        scanner_factory:
            Optional override for :class:`bleak.BleakScanner` —
            primarily for tests. Receives the detection callback and
            must return an object satisfying :class:`_BleakScannerLike`.
        """
        self._scanner_factory = scanner_factory or _default_scanner_factory

    def scan(
        self,
        *,
        matcher: Matcher = accept_all,
        timeout: float | None = None,
    ) -> AsyncIterator[Candidate]:
        """Yield BLE candidates as advertisements arrive (see :meth:`Scanner.scan`)."""
        queue: asyncio.Queue[Candidate] = asyncio.Queue()
        seen: set[str] = set()

        def _on_detection(device: Any, advertisement_data: Any) -> None:
            address = getattr(device, "address", None)
            if not isinstance(address, str) or address in seen:
                return
            advertised_name = getattr(advertisement_data, "local_name", None) or getattr(
                device, "name", None
            )
            metadata = {
                "rssi": getattr(advertisement_data, "rssi", None),
                "service_uuids": tuple(getattr(advertisement_data, "service_uuids", ()) or ()),
                "manufacturer_data": dict(
                    getattr(advertisement_data, "manufacturer_data", {}) or {}
                ),
            }
            candidate = Candidate(
                transport="ble",
                address=address,
                advertised_name=advertised_name,
                metadata=metadata,
            )
            if not matcher(candidate):
                return
            seen.add(address)
            queue.put_nowait(candidate)

        try:
            scanner = self._scanner_factory(_on_detection)
        except Exception as exc:
            raise TransportError(f"BleScanner: failed to construct bleak scanner: {exc}") from exc

        async def _aiter() -> AsyncIterator[Candidate]:
            try:
                await scanner.start()
            except Exception as exc:
                raise TransportError(f"BleScanner: failed to start scan: {exc}") from exc
            try:
                while True:
                    if timeout is None:
                        candidate = await queue.get()
                    else:
                        try:
                            candidate = await asyncio.wait_for(queue.get(), timeout=timeout)
                        except TimeoutError:
                            return
                    yield candidate
            finally:
                with contextlib.suppress(Exception):
                    await scanner.stop()

        return _aiter()
