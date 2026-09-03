"""Lightweight helpers for sandboxes, tutorials, and tests.

This submodule is part of the public runtime surface but is intended
for non-production use — its contents trade durability for brevity.
"""

from __future__ import annotations

from kandra_runtime.identity import Identity, IdentityNotFoundError, IdentityStore


class MemoryIdentityStore(IdentityStore):
    """In-memory :class:`IdentityStore` — no disk side effects.

    Intended for tests, examples, and exploratory sandbox scripts.
    Not safe across processes; not durable across runs. For production
    use see :class:`~kandra_runtime.PlatformDirsJsonStore`.
    """

    def __init__(self) -> None:
        """Initialize an empty in-memory record table."""
        self._records: dict[str, Identity] = {}

    def save(self, identity: Identity) -> None:
        """Persist ``identity`` keyed by its ``saved_name``."""
        self._records[identity.saved_name] = identity

    def load(self, saved_name: str) -> Identity:
        """Return the identity stored under ``saved_name``.

        Raises :class:`IdentityNotFoundError` if no such entry exists.
        """
        try:
            return self._records[saved_name]
        except KeyError as exc:
            raise IdentityNotFoundError(saved_name) from exc

    def delete(self, saved_name: str) -> None:
        """Remove the entry stored under ``saved_name`` (idempotent)."""
        self._records.pop(saved_name, None)

    def list_saved(self) -> list[Identity]:
        """Return every persisted identity in insertion order."""
        return list(self._records.values())


__all__ = ["MemoryIdentityStore"]
