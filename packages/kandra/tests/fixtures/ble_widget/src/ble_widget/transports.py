"""Placeholder transport adapters for the BLE+HTTP fixture device.

Both conform to ``kandra_runtime.Transport`` so the generator can import and
reference them; real I/O raises ``NotImplementedError``. Tests swap in in-memory
fakes for the generated ``BleTransport`` / ``HttpTransport`` symbols.
"""

from __future__ import annotations

from typing import Any


class HttpAdapter:
    """HTTP transport stub. Does not perform I/O."""

    def __init__(self, *, base_url: str | None = None, **_: Any) -> None:
        """Capture the base URL from the manifest transport config block."""
        self._base_url = base_url

    async def open(self) -> None:
        """No-op; not a real backend."""

    async def close(self) -> None:
        """No-op; not a real backend."""

    async def request(self, payload: bytes) -> bytes:
        """Reject all I/O -- this is a fixture placeholder."""
        raise NotImplementedError("HttpAdapter is a fixture placeholder")


class BleAdapter:
    """BLE transport stub. Does not perform I/O."""

    async def open(self) -> None:
        """No-op; not a real backend."""

    async def close(self) -> None:
        """No-op; not a real backend."""

    async def request(self, payload: bytes) -> bytes:
        """Reject all I/O -- this is a fixture placeholder."""
        raise NotImplementedError("BleAdapter is a fixture placeholder")
