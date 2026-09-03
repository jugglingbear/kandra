"""Structured device identities and the :class:`IdentityStore` protocol.

An *identity* is the small bundle of facts a generated client needs to
reconnect to a previously-enrolled device without rediscovering it: a
BLE MAC + friendly name, an HTTP base URL + auth token, the Wi-Fi
credentials a camera handed back during pairing, etc.

Identities are modeled as **pydantic discriminated-union records** so
the ``kandra list-saved-devices`` CLI can render human-readable rows
without instantiating each transport adapter. The discriminator is the
``transport`` field, populated automatically by the per-class
``model_config``.

BLE bond keys are deliberately *not* part of any identity record —
reconnection delegates to the host OS bond cache (CoreBluetooth
keychain on macOS, ``/var/lib/bluetooth/`` on Linux). Stacks that need
application-managed bond material can subclass :class:`BleIdentity` and
register a custom :class:`IdentityStore` codec.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kandra_runtime.errors import KandraError

# ---------------------------------------------------------------------------
# Identity record schema (discriminated union on `transport`).
# ---------------------------------------------------------------------------


class _IdentityBase(BaseModel):
    """Common shape for every persisted identity record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    saved_name: str = Field(min_length=1, description="User-chosen nickname.")
    enrolled_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp captured when the identity was first persisted.",
    )


class BleIdentity(_IdentityBase):
    """Reconnection facts for a BLE peripheral.

    ``address`` is the BLE MAC (or, on macOS, the CoreBluetooth-assigned
    UUID). Bond keys live in the OS keychain — see the module docstring.
    """

    transport: Literal["ble"] = "ble"
    address: str = Field(min_length=1, description="BLE peripheral address (MAC or CB UUID).")
    advertised_name: str | None = Field(
        default=None,
        description="Advertising name observed at enrollment time; informational only.",
    )


class HttpIdentity(_IdentityBase):
    """Reconnection facts for an HTTP-based device or service."""

    transport: Literal["http"] = "http"
    base_url: str = Field(min_length=1, description="Root URL the client should target.")
    auth_token: str | None = Field(
        default=None,
        description="Bearer token or session cookie captured during enrollment.",
    )


class WifiCredentials(BaseModel):
    """SSID + password handed back by a device during pairing.

    Modeled separately from :class:`BleIdentity` because some devices
    expose Wi-Fi credentials over HTTP as well. Attach via
    :class:`CompositeIdentity` when a single saved device needs more
    than one transport's worth of facts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ssid: str = Field(min_length=1)
    password: str = Field(min_length=0)  # WPA allows >=0; empty == open network


class CompositeIdentity(_IdentityBase):
    """A device that needs more than one set of reconnection facts.

    Cameras typically need both a BLE bond (for control) and Wi-Fi
    credentials (for media transfer); a wall-plug switch might pair via
    BLE and then expose an HTTP API once joined to the LAN. The
    ``components`` map keys are arbitrary labels chosen by the device
    author (``"control"``, ``"media"``, ``"ap"``) — the generated
    client surfaces them as constructor arguments to
    :meth:`Transport.from_identity`.
    """

    transport: Literal["composite"] = "composite"
    components: dict[str, Identity] = Field(
        default_factory=dict,
        description="Sub-identities keyed by author-chosen role label.",
    )
    wifi: WifiCredentials | None = Field(
        default=None,
        description="Optional Wi-Fi credentials handed back during pairing.",
    )


# Discriminated union — pydantic picks the concrete class by the
# ``transport`` literal at parse time.
Identity = Annotated[
    BleIdentity | HttpIdentity | CompositeIdentity,
    Field(discriminator="transport"),
]


# ---------------------------------------------------------------------------
# Error type.
# ---------------------------------------------------------------------------


class IdentityNotFoundError(KandraError, LookupError):
    """Raised when :meth:`IdentityStore.load` cannot find a saved name.

    Subclasses the standard ``LookupError`` so code that catches the
    built-in still works.
    """


# ---------------------------------------------------------------------------
# IdentityStore protocol.
# ---------------------------------------------------------------------------


@runtime_checkable
class IdentityStore(Protocol):
    """Persistent map of ``saved_name -> Identity``.

    Implementations must be safe to share across coroutines but are not
    required to be safe across processes; the default
    :class:`~kandra_runtime.identity_store_file.PlatformDirsJsonStore`
    uses an atomic-rename write strategy that is safe for the typical
    single-process CLI / client case.
    """

    def save(self, identity: Identity) -> None:
        """Persist ``identity`` keyed by its ``saved_name``.

        Overwrites any prior entry with the same name.
        """

    def load(self, saved_name: str) -> Identity:
        """Return the identity stored under ``saved_name``.

        Raises :class:`IdentityNotFoundError` if no such entry exists.
        """

    def delete(self, saved_name: str) -> None:
        """Remove the entry stored under ``saved_name``.

        Idempotent — deleting a missing name must not raise.
        """

    def list_saved(self) -> list[Identity]:
        """Return every persisted identity in arbitrary order."""
