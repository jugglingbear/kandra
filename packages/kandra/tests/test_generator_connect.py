"""Tests for the generated ``connect()`` / ``list_saved()`` machinery (Todo 8).

Builds the example SDK, swaps the generated module's ``BleTransport`` and
``HttpTransport`` symbols for in-memory fakes, pre-populates an identity
store, and exercises the lifecycle.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from kandra.generator import build_sdk

EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "pneumatic_bear_poker"
EXAMPLE_MANIFEST = EXAMPLE_DIR / "manifest.yaml"
EXAMPLE_SRC = EXAMPLE_DIR / "src"


@pytest.fixture
def sdk_on_path(tmp_path: Path) -> Iterator[Path]:
    """Build the example SDK into ``tmp_path``; expose it on ``sys.path``."""
    result = build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path)
    added = [str(EXAMPLE_SRC), str(tmp_path)]
    sys.path[:0] = added
    snapshot = set(sys.modules)
    try:
        yield result.package_path
    finally:
        for p in added:
            with suppress(ValueError):
                sys.path.remove(p)
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]


class _FakeTransport:
    """Minimal Transport stand-in: tracks ``open()`` / ``close()`` calls."""

    def __init__(self, family: str, identity: Any) -> None:
        self.family = family
        self.identity = identity
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def patched_client(
    sdk_on_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    """Yield the generated client module with BLE/HTTP transport factories faked."""
    import pneumatic_bear_poker_sdk.client as client_mod

    built: list[_FakeTransport] = []

    class _FakeBleTransport:
        @staticmethod
        def from_identity(identity: Any, *, channels: Any) -> _FakeTransport:
            t = _FakeTransport("ble", identity)
            built.append(t)
            return t

    class _FakeHttpTransport:
        @staticmethod
        def from_identity(identity: Any) -> _FakeTransport:
            t = _FakeTransport("http", identity)
            built.append(t)
            return t

    monkeypatch.setattr(client_mod, "BleTransport", _FakeBleTransport)
    monkeypatch.setattr(client_mod, "HttpTransport", _FakeHttpTransport)

    # Re-evaluate _TRANSPORT_FACTORIES so its lambdas pick up the patched symbols.
    from pneumatic_bear_poker_sdk.transports import TransportId

    monkeypatch.setattr(
        client_mod,
        "_TRANSPORT_FACTORIES",
        (
            (
                TransportId.BLE,
                "ble",
                lambda ident: _FakeBleTransport.from_identity(
                    ident, channels=client_mod._BLE_CHANNELS_ble
                ),
            ),
            (TransportId.HTTP, "http", lambda ident: _FakeHttpTransport.from_identity(ident)),
        ),
    )

    client_mod._built_transports_for_tests = built
    yield client_mod


def _make_store(tmp_path: Path) -> Any:
    from kandra_runtime import PlatformDirsJsonStore

    return PlatformDirsJsonStore(app_name="pneumatic_bear_poker_sdk", directory=tmp_path / "store")


async def test_connect_activates_both_families_from_composite_identity(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """A CompositeIdentity supplies BLE+HTTP → both transports open."""
    from kandra_runtime import BleIdentity, CompositeIdentity, HttpIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    store = _make_store(tmp_path)
    store.save(
        CompositeIdentity(
            saved_name="bear-1",
            components={
                "control": BleIdentity(
                    saved_name="bear-1", address="AA:BB:CC:DD:EE:FF", advertised_name="Bear"
                ),
                "api": HttpIdentity(saved_name="bear-1", base_url="http://10.0.0.1:8080"),
            },
        )
    )

    client = await PneumaticBearPokerClient.connect("bear-1", store=store)
    try:
        assert set(client._transports.keys()) == {TransportId.BLE, TransportId.HTTP}
        assert set(client._owned_transports.keys()) == {TransportId.BLE, TransportId.HTTP}
        for t in client._transports.values():
            assert t.opened is True
            assert t.closed is False
    finally:
        await client.aclose()
    for t in patched_client._built_transports_for_tests:
        assert t.closed is True


async def test_connect_with_ble_only_identity_skips_http(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """A plain BleIdentity activates only the BLE transport."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="bear-2", address="11:22:33:44:55:66", advertised_name="Bear"))

    client = await PneumaticBearPokerClient.connect("bear-2", store=store)
    try:
        assert set(client._transports.keys()) == {TransportId.BLE}
    finally:
        await client.aclose()


async def test_connect_transports_filter_narrows_activation(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """``transports={BLE}`` skips HTTP even when the identity has both."""
    from kandra_runtime import BleIdentity, CompositeIdentity, HttpIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    store = _make_store(tmp_path)
    store.save(
        CompositeIdentity(
            saved_name="bear-3",
            components={
                "control": BleIdentity(
                    saved_name="bear-3", address="AA:BB:CC:DD:EE:FF", advertised_name="Bear"
                ),
                "api": HttpIdentity(saved_name="bear-3", base_url="http://10.0.0.1:8080"),
            },
        )
    )

    client = await PneumaticBearPokerClient.connect(
        "bear-3", store=store, transports={TransportId.BLE}
    )
    try:
        assert set(client._transports.keys()) == {TransportId.BLE}
    finally:
        await client.aclose()


async def test_connect_raises_when_identity_has_no_matching_family(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """Filtering down to a family the identity lacks raises ValueError."""
    from kandra_runtime import HttpIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    store = _make_store(tmp_path)
    store.save(HttpIdentity(saved_name="bear-4", base_url="http://10.0.0.1:8080"))

    with pytest.raises(ValueError, match="no transports"):
        await PneumaticBearPokerClient.connect(
            "bear-4", store=store, transports={TransportId.BLE}
        )


async def test_connect_propagates_identity_not_found(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """An unknown saved_name surfaces IdentityNotFoundError from the store."""
    from kandra_runtime import IdentityNotFoundError
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = _make_store(tmp_path)

    with pytest.raises(IdentityNotFoundError):
        await PneumaticBearPokerClient.connect("missing", store=store)


def test_list_saved_returns_saved_names(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """``list_saved`` returns the friendly names the store knows about."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="alpha", address="AA:BB:CC:DD:EE:01", advertised_name="A"))
    store.save(BleIdentity(saved_name="beta", address="AA:BB:CC:DD:EE:02", advertised_name="B"))

    assert sorted(PneumaticBearPokerClient.list_saved(store=store)) == ["alpha", "beta"]


async def test_async_context_manager_closes_owned_transports(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """``async with connect(...) as client:`` closes transports on exit."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="bear-5", address="AA:BB:CC:DD:EE:FF", advertised_name="Bear"))

    async with await PneumaticBearPokerClient.connect("bear-5", store=store) as client:
        assert client._owned_transports
    for t in patched_client._built_transports_for_tests:
        assert t.closed is True


async def test_constructor_supplied_transports_are_not_closed(
    patched_client: Any,
) -> None:
    """Transports passed to ``__init__`` belong to the caller; ``aclose`` must not close them."""
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    user_owned = _FakeTransport("ble", identity=None)
    await user_owned.open()

    client = PneumaticBearPokerClient(transports={TransportId.BLE: user_owned})
    await client.aclose()

    assert user_owned.closed is False


# ---------------------------------------------------------------------------
# discover_and_connect() — one-shot scan + enroll + save + connect
# ---------------------------------------------------------------------------


class _FakeEnrollment:
    """Records the candidate it was handed and returns a pre-canned Identity."""

    def __init__(self, identity: Any) -> None:
        self._identity = identity
        self.calls: list[tuple[Any, str]] = []

    async def enroll(self, candidate: Any, *, saved_name: str) -> Any:
        self.calls.append((candidate, saved_name))
        return self._identity


def _make_candidate(transport: str, address: str) -> Any:
    from kandra_runtime import Candidate

    return Candidate(transport=transport, address=address, advertised_name="fake")


async def test_discover_and_connect_uses_saved_identity_when_present(
    patched_client: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fast path: a saved identity short-circuits the scan + enroll dance."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = _make_store(tmp_path)
    store.save(
        BleIdentity(saved_name="kitchen", address="AA:BB:CC:DD:EE:FF", advertised_name="K")
    )

    scan_called = {"ble": 0, "http": 0}

    async def fake_scan_ble(**_kwargs: Any) -> list[Any]:
        scan_called["ble"] += 1
        return []

    async def fake_scan_http(**_kwargs: Any) -> list[Any]:
        scan_called["http"] += 1
        return []

    monkeypatch.setattr(patched_client, "scan_ble", fake_scan_ble)
    monkeypatch.setattr(patched_client, "scan_http", fake_scan_http)

    enroll = _FakeEnrollment(identity=None)
    client = await PneumaticBearPokerClient.discover_and_connect(
        "kitchen", enrollment={"ble": enroll}, store=store
    )
    try:
        assert scan_called == {"ble": 0, "http": 0}
        assert enroll.calls == []
    finally:
        await client.aclose()


async def test_discover_and_connect_scans_enrolls_and_saves_on_first_run(
    patched_client: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First run: scan both families, enroll each, persist a CompositeIdentity, connect."""
    from kandra_runtime import BleIdentity, HttpIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    store = _make_store(tmp_path)

    ble_candidate = _make_candidate("ble", "AA:BB:CC:DD:EE:FF")
    http_candidate = _make_candidate("http", "http://10.0.0.1:8080")

    async def fake_scan_ble(**_kwargs: Any) -> list[Any]:
        return [ble_candidate]

    async def fake_scan_http(**_kwargs: Any) -> list[Any]:
        return [http_candidate]

    monkeypatch.setattr(patched_client, "scan_ble", fake_scan_ble)
    monkeypatch.setattr(patched_client, "scan_http", fake_scan_http)

    ble_ident = BleIdentity(
        saved_name="kitchen", address="AA:BB:CC:DD:EE:FF", advertised_name="K"
    )
    http_ident = HttpIdentity(saved_name="kitchen", base_url="http://10.0.0.1:8080")

    client = await PneumaticBearPokerClient.discover_and_connect(
        "kitchen",
        enrollment={"ble": _FakeEnrollment(ble_ident), "http": _FakeEnrollment(http_ident)},
        store=store,
    )
    try:
        assert set(client._transports.keys()) == {TransportId.BLE, TransportId.HTTP}
        saved = store.load("kitchen")
        # Multi-family -> wrapped in CompositeIdentity
        from kandra_runtime import CompositeIdentity

        assert isinstance(saved, CompositeIdentity)
        assert set(saved.components.keys()) == {"ble", "http"}
    finally:
        await client.aclose()


async def test_discover_and_connect_single_family_unwraps_to_plain_identity(
    patched_client: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only one family enrolls, the saved record is the bare sub-identity (not Composite)."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = _make_store(tmp_path)

    async def fake_scan_ble(**_kwargs: Any) -> list[Any]:
        return [_make_candidate("ble", "AA:BB:CC:DD:EE:FF")]

    monkeypatch.setattr(patched_client, "scan_ble", fake_scan_ble)

    ble_ident = BleIdentity(
        saved_name="ble-only", address="AA:BB:CC:DD:EE:FF", advertised_name="X"
    )
    client = await PneumaticBearPokerClient.discover_and_connect(
        "ble-only", enrollment={"ble": _FakeEnrollment(ble_ident)}, store=store
    )
    try:
        saved = store.load("ble-only")
        assert isinstance(saved, BleIdentity)
        assert saved.address == "AA:BB:CC:DD:EE:FF"
    finally:
        await client.aclose()


async def test_discover_and_connect_raises_when_no_candidates(
    patched_client: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty scan results -> EnrollmentError."""
    from kandra_runtime import BleIdentity, EnrollmentError
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = _make_store(tmp_path)

    async def fake_scan_ble(**_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(patched_client, "scan_ble", fake_scan_ble)

    ble_ident = BleIdentity(saved_name="x", address="A", advertised_name="x")
    with pytest.raises(EnrollmentError, match="no BLE candidates"):
        await PneumaticBearPokerClient.discover_and_connect(
            "x", enrollment={"ble": _FakeEnrollment(ble_ident)}, store=store
        )


async def test_discover_and_connect_rejects_single_enrollment_for_multi_family(
    patched_client: Any,
    tmp_path: Path,
) -> None:
    """Single-Enrollment shortcut is ambiguous for a multi-family device."""
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="multiple discoverable"):
        await PneumaticBearPokerClient.discover_and_connect(
            "x", enrollment=_FakeEnrollment(identity=None), store=store
        )
