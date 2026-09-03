"""Default :class:`IdentityStore` implementation: per-SDK JSON file under platformdirs.

Each SDK installation gets its own directory (chosen by ``app_name``)
under the OS-appropriate user-data root from :mod:`platformdirs`. The
file is a single JSON document mapping ``saved_name`` to the discriminated
identity record (see :mod:`kandra_runtime.identity`).

The writer uses **atomic-rename**: it writes to a temp sibling in the
same directory, fsyncs, then ``os.replace`` over the target. This makes
concurrent reads safe (they always observe either the prior or the new
file in its entirety, never a torn write) and crash-safe for the typical
single-process CLI / client case.

This module is *not* thread-safe for concurrent writers in the same
process. Wrap the store in an external lock if multiple coroutines /
threads need to write simultaneously.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from platformdirs import user_data_dir
from pydantic import TypeAdapter, ValidationError

from kandra_runtime.identity import (
    Identity,
    IdentityNotFoundError,
    IdentityStore,
)

_IDENTITY_LIST_ADAPTER: TypeAdapter[list[Identity]] = TypeAdapter(list[Identity])
_STORE_FILENAME = "identities.json"


class PlatformDirsJsonStore(IdentityStore):
    """JSON-backed :class:`IdentityStore` rooted at ``platformdirs.user_data_dir``.

    The on-disk file lives at
    ``<user_data_dir(app_name)>/identities.json`` and contains a JSON
    array of identity records. The full file is rewritten on every
    :meth:`save` / :meth:`delete` — fine for the typical "tens to low
    hundreds of saved devices" case this targets.
    """

    def __init__(self, *, app_name: str, directory: Path | None = None) -> None:
        """Create a store for ``app_name`` (typically the generated SDK's package name).

        Parameters
        ----------
        app_name:
            Name that disambiguates this SDK's identity directory from
            other apps on the same machine. Generated clients pass their
            own package name (e.g. ``"pneumatic_bear_poker_sdk"``).
        directory:
            Optional override for the storage root — primarily for
            tests. When ``None``, the directory is resolved via
            ``platformdirs.user_data_dir(app_name)``.
        """
        if not app_name:
            raise ValueError("app_name must be a non-empty string")
        self._app_name = app_name
        self._directory = directory if directory is not None else Path(user_data_dir(app_name))
        self._path = self._directory / _STORE_FILENAME

    # -- Public IdentityStore surface --------------------------------------

    @property
    def path(self) -> Path:
        """Absolute path of the JSON file backing this store."""
        return self._path

    def save(self, identity: Identity) -> None:
        """Persist ``identity``, overwriting any prior entry with the same name."""
        records = {item.saved_name: item for item in self._read()}
        records[identity.saved_name] = identity
        self._write(list(records.values()))

    def load(self, saved_name: str) -> Identity:
        """Return the identity stored under ``saved_name`` or raise IdentityNotFoundError."""
        for item in self._read():
            if item.saved_name == saved_name:
                return item
        raise IdentityNotFoundError(
            f"No identity saved under {saved_name!r} in {self._path}"
        )

    def delete(self, saved_name: str) -> None:
        """Remove the identity stored under ``saved_name`` (no-op if absent)."""
        records = [item for item in self._read() if item.saved_name != saved_name]
        self._write(records)

    def list_saved(self) -> list[Identity]:
        """Return every persisted identity in arbitrary order."""
        return self._read()

    # -- Internal I/O ------------------------------------------------------

    def _read(self) -> list[Identity]:
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - rare
            raise OSError(f"Cannot read identity store at {self._path}: {exc}") from exc
        if not raw.strip():
            return []
        try:
            return _IDENTITY_LIST_ADAPTER.validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise ValueError(
                f"Identity store at {self._path} is corrupt or schema-incompatible: {exc}"
            ) from exc

    def _write(self, records: list[Identity]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = _IDENTITY_LIST_ADAPTER.dump_json(records, indent=2)
        # Atomic write: tempfile in same directory + os.replace.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".identities-",
            suffix=".json.tmp",
            dir=self._directory,
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            # Best-effort cleanup; don't shadow the original exception.
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
