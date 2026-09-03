"""Tests for the in-memory IdentityStore shipped under ``kandra_runtime.testing``."""

from __future__ import annotations

import pytest
from kandra_runtime import (
    BleIdentity,
    CompositeIdentity,
    HttpIdentity,
    IdentityNotFoundError,
)
from kandra_runtime.testing import MemoryIdentityStore


@pytest.fixture
def store() -> MemoryIdentityStore:
    return MemoryIdentityStore()


def test_load_missing_raises_identity_not_found(store: MemoryIdentityStore) -> None:
    with pytest.raises(IdentityNotFoundError):
        store.load("does-not-exist")


def test_save_then_load_roundtrip(store: MemoryIdentityStore) -> None:
    ident = BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF", advertised_name="PBP-001")
    store.save(ident)
    loaded = store.load("poker")
    assert loaded == ident
    # And the same object reference too — no copy on the way out.
    assert loaded is ident


def test_save_overwrites_same_name(store: MemoryIdentityStore) -> None:
    store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))
    store.save(BleIdentity(saved_name="poker", address="11:22:33:44:55:66"))
    loaded = store.load("poker")
    assert isinstance(loaded, BleIdentity)
    assert loaded.address == "11:22:33:44:55:66"
    assert len(store.list_saved()) == 1


def test_list_saved_returns_all_identities(store: MemoryIdentityStore) -> None:
    store.save(BleIdentity(saved_name="a", address="AA:BB:CC:DD:EE:01"))
    store.save(HttpIdentity(saved_name="b", base_url="http://192.168.1.2"))
    store.save(
        CompositeIdentity(
            saved_name="c",
            components={
                "ble": BleIdentity(saved_name="c-ble", address="AA:BB:CC:DD:EE:02"),
            },
        )
    )
    saved = store.list_saved()
    assert {s.saved_name for s in saved} == {"a", "b", "c"}
    assert {s.transport for s in saved} == {"ble", "http", "composite"}


def test_list_saved_preserves_insertion_order(store: MemoryIdentityStore) -> None:
    for i in range(5):
        store.save(BleIdentity(saved_name=f"dev-{i}", address=f"AA:BB:CC:DD:EE:{i:02X}"))
    assert [s.saved_name for s in store.list_saved()] == [f"dev-{i}" for i in range(5)]


def test_delete_existing(store: MemoryIdentityStore) -> None:
    store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))
    store.delete("poker")
    assert store.list_saved() == []
    with pytest.raises(IdentityNotFoundError):
        store.load("poker")


def test_delete_is_idempotent(store: MemoryIdentityStore) -> None:
    store.delete("never-saved")  # must not raise
    store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))
    store.delete("poker")
    store.delete("poker")  # second delete also fine
    assert store.list_saved() == []


def test_independent_instances_do_not_share_state() -> None:
    a = MemoryIdentityStore()
    b = MemoryIdentityStore()
    a.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))
    assert a.list_saved() != []
    assert b.list_saved() == []
    with pytest.raises(IdentityNotFoundError):
        b.load("poker")


def test_satisfies_identity_store_protocol() -> None:
    """Structural check: a MemoryIdentityStore is usable wherever IdentityStore is."""
    from kandra_runtime import IdentityStore

    store: IdentityStore = MemoryIdentityStore()
    store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))
    assert store.load("poker").saved_name == "poker"
