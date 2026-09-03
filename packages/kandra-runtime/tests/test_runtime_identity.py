"""Tests for structured identity records and IdentityStore persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from kandra_runtime import (
    BleIdentity,
    CompositeIdentity,
    HttpIdentity,
    IdentityNotFoundError,
    PlatformDirsJsonStore,
    WifiCredentials,
)
from kandra_runtime.identity import Identity
from pydantic import TypeAdapter, ValidationError


def test_ble_identity_discriminator_default() -> None:
    ident = BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF")
    assert ident.transport == "ble"
    assert ident.advertised_name is None


def test_http_identity_token_optional() -> None:
    ident = HttpIdentity(saved_name="poker", base_url="http://10.0.0.1")
    assert ident.transport == "http"
    assert ident.auth_token is None


def test_identity_discriminated_union_roundtrip() -> None:
    adapter: TypeAdapter[list[Identity]] = TypeAdapter(list[Identity])
    originals: list[Identity] = [
        BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"),
        HttpIdentity(saved_name="cloud", base_url="https://api.example.com", auth_token="tok"),
        CompositeIdentity(
            saved_name="bear-camera",
            components={
                "control": BleIdentity(saved_name="bear-camera-ble", address="11:22:33:44:55:66"),
                "media": HttpIdentity(saved_name="bear-camera-http", base_url="http://10.5.5.9"),
            },
            wifi=WifiCredentials(ssid="bear-net", password="secret"),
        ),
    ]
    encoded = adapter.dump_json(originals)
    restored = adapter.validate_json(encoded)
    assert restored == originals


def test_empty_saved_name_rejected() -> None:
    with pytest.raises(ValidationError):
        BleIdentity(saved_name="", address="AA:BB:CC:DD:EE:FF")


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        BleIdentity(
            saved_name="poker",
            address="AA:BB:CC:DD:EE:FF",
            extra_field="nope",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# PlatformDirsJsonStore
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> PlatformDirsJsonStore:
    return PlatformDirsJsonStore(app_name="kandra_test_sdk", directory=tmp_path)


def test_load_missing_raises_identity_not_found(store: PlatformDirsJsonStore) -> None:
    with pytest.raises(IdentityNotFoundError):
        store.load("does-not-exist")


def test_save_then_load_roundtrip(store: PlatformDirsJsonStore) -> None:
    ident = BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF", advertised_name="PBP-001")
    store.save(ident)
    loaded = store.load("poker")
    assert loaded == ident
    # File actually written.
    assert store.path.exists()


def test_save_overwrites_same_name(store: PlatformDirsJsonStore) -> None:
    store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))
    store.save(BleIdentity(saved_name="poker", address="11:22:33:44:55:66"))
    loaded = store.load("poker")
    assert isinstance(loaded, BleIdentity)
    assert loaded.address == "11:22:33:44:55:66"
    # Only one entry, not two.
    assert len(store.list_saved()) == 1


def test_list_saved_returns_all(store: PlatformDirsJsonStore) -> None:
    store.save(BleIdentity(saved_name="a", address="AA:BB:CC:DD:EE:01"))
    store.save(HttpIdentity(saved_name="b", base_url="http://192.168.1.2"))
    saved = store.list_saved()
    assert {s.saved_name for s in saved} == {"a", "b"}
    assert {s.transport for s in saved} == {"ble", "http"}


def test_delete_is_idempotent(store: PlatformDirsJsonStore) -> None:
    store.delete("never-saved")  # must not raise
    store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))
    store.delete("poker")
    store.delete("poker")  # second delete also fine
    assert store.list_saved() == []


def test_corrupt_file_raises(tmp_path: Path) -> None:
    store = PlatformDirsJsonStore(app_name="kandra_test", directory=tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        store.list_saved()


def test_empty_file_is_treated_as_no_records(tmp_path: Path) -> None:
    store = PlatformDirsJsonStore(app_name="kandra_test", directory=tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("", encoding="utf-8")
    assert store.list_saved() == []


def test_empty_app_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="app_name"):
        PlatformDirsJsonStore(app_name="", directory=tmp_path)


def test_atomic_write_no_partial_state_visible(tmp_path: Path) -> None:
    """Round-trip many records to exercise the write+replace path."""
    store = PlatformDirsJsonStore(app_name="kandra_test", directory=tmp_path)
    for i in range(20):
        store.save(BleIdentity(saved_name=f"dev-{i:02d}", address=f"AA:BB:CC:DD:EE:{i:02X}"))
    assert len(store.list_saved()) == 20
    # No leftover .tmp files (the write path uses NamedTemporaryFile + replace).
    leftover = list(tmp_path.glob(".identities-*.json.tmp"))
    assert leftover == [], f"leftover temp files: {leftover}"
