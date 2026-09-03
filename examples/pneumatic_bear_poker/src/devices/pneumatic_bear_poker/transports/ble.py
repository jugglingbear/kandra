"""Placeholder BLE transport for the Pneumatic Bear Poker.

Conforms to `kandra_runtime.Transport` so the generator can import and
reference it, but actual I/O raises `NotImplementedError` until a real
BLE backend lands.
"""

from __future__ import annotations


class BleakAdapter:
    """BLE transport stub. Does not perform I/O yet."""

    async def open(self) -> None:
        """No-op; BLE backend not yet implemented."""

    async def close(self) -> None:
        """No-op; BLE backend not yet implemented."""

    async def request(self, payload: bytes) -> bytes:
        """Reject all I/O until the BLE backend is wired up."""
        raise NotImplementedError("BleakAdapter is a placeholder; real BLE I/O is not implemented")
