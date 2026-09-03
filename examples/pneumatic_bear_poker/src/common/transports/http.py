"""Placeholder HTTP transport adapter.

Conforms to `kandra_runtime.Transport`; real I/O raises
`NotImplementedError` until an httpx-backed implementation lands.
"""

from __future__ import annotations

from typing import Any


class HttpxAdapter:
    """HTTP transport stub. Does not perform I/O yet."""

    def __init__(self, *, base_url: str | None = None, **_: Any) -> None:
        """Capture the base URL from the manifest's transport config block."""
        self._base_url = base_url

    async def open(self) -> None:
        """No-op; HTTP backend not yet implemented."""

    async def close(self) -> None:
        """No-op; HTTP backend not yet implemented."""

    async def request(self, payload: bytes) -> bytes:
        """Reject all I/O until the HTTP backend is wired up."""
        raise NotImplementedError("HttpxAdapter is a placeholder; real HTTP I/O is not implemented")
