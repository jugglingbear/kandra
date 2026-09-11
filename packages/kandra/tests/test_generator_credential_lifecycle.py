"""Tests for the generated credential-lifecycle machinery (on_stale recovery + re_enroll).

Builds the example SDK, swaps its ``BleTransport`` / ``HttpTransport`` for fakes
whose ``open()`` can be made to raise :class:`IdentityStaleError`, and exercises
``connect(on_stale=...)`` recovery, ``re_enroll``, and the ``last_validated`` stamp.
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
    """Transport stand-in whose ``open()`` optionally raises IdentityStaleError."""

    def __init__(self, family: str, identity: Any, control: dict[str, bool]) -> None:
        self.family = family
        self.identity = identity
        self._control = control
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        from kandra_runtime import IdentityStaleError

        if self._control.get(f"stale_{self.family}"):
            raise IdentityStaleError(f"{self.family} credentials rejected")
        self.opened = True

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def patched(sdk_on_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, dict[str, bool]]]:
    """Yield (client_module, control) with transport factories faked; ``control`` toggles staleness."""
    import pneumatic_bear_poker_sdk.client as client_mod
    from pneumatic_bear_poker_sdk.transports import TransportId

    control: dict[str, bool] = {}
    built: list[_FakeTransport] = []

    def _ble_factory(ident: Any) -> _FakeTransport:
        t = _FakeTransport("ble", ident, control)
        built.append(t)
        return t

    def _http_factory(ident: Any) -> _FakeTransport:
        t = _FakeTransport("http", ident, control)
        built.append(t)
        return t

    monkeypatch.setattr(
        client_mod,
        "_TRANSPORT_FACTORIES",
        (
            (TransportId.BLE, "ble", _ble_factory),
            (TransportId.HTTP, "http", _http_factory),
        ),
    )
    client_mod._built_for_tests = built
    yield client_mod, control


def _make_store(tmp_path: Path) -> Any:
    from kandra_runtime import PlatformDirsJsonStore

    return PlatformDirsJsonStore(app_name="pneumatic_bear_poker_sdk", directory=tmp_path / "store")


class _FakeEnrollment:
    def __init__(self, identity: Any) -> None:
        self._identity = identity
        self.calls: list[str] = []

    async def enroll(self, candidate: Any, *, saved_name: str) -> Any:
        self.calls.append(saved_name)
        return self._identity


def _candidate(transport: str, address: str) -> Any:
    from kandra_runtime import Candidate

    return Candidate(transport=transport, address=address, advertised_name="fake")


# ---------------------------------------------------------------------------
# connect(on_stale=...)
# ---------------------------------------------------------------------------


async def test_connect_on_stale_recovers_and_retries(
    patched: tuple[Any, dict[str, bool]],
    tmp_path: Path,
) -> None:
    """A stale bond at open() invokes on_stale, then the connect retries and succeeds."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    client_mod, control = patched
    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="bear", address="AA:BB:CC:DD:EE:FF"))
    control["stale_ble"] = True  # first open() raises IdentityStaleError

    recovered: list[str] = []

    async def on_stale(name: str) -> Any:
        recovered.append(name)
        control["stale_ble"] = False  # clear so the retry opens cleanly
        return store.load(name)

    client = await PneumaticBearPokerClient.connect("bear", store=store, on_stale=on_stale)
    try:
        assert recovered == ["bear"]
        assert set(client._transports.keys()) == {TransportId.BLE}
    finally:
        await client.aclose()
    # Two BLE transports were built: the stale one (closed on failure) + the retry.
    assert len(client_mod._built_for_tests) == 2


async def test_connect_without_on_stale_propagates(
    patched: tuple[Any, dict[str, bool]],
    tmp_path: Path,
) -> None:
    """Without an on_stale callback, IdentityStaleError from open() propagates."""
    from kandra_runtime import BleIdentity, IdentityStaleError
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    _client_mod, control = patched
    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="bear", address="AA:BB:CC:DD:EE:FF"))
    control["stale_ble"] = True

    with pytest.raises(IdentityStaleError):
        await PneumaticBearPokerClient.connect("bear", store=store)


async def test_connect_stamps_last_validated(
    patched: tuple[Any, dict[str, bool]],
    tmp_path: Path,
) -> None:
    """A successful connect stamps ``last_validated`` on the persisted identity."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    _client_mod, _control = patched
    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="bear", address="AA:BB:CC:DD:EE:FF"))
    assert store.load("bear").last_validated is None

    client = await PneumaticBearPokerClient.connect("bear", store=store)
    try:
        assert store.load("bear").last_validated is not None
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# re_enroll
# ---------------------------------------------------------------------------


async def test_re_enroll_rescans_and_overwrites(
    patched: tuple[Any, dict[str, bool]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """re_enroll re-discovers the device, re-enrolls, and atomically replaces the record."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    client_mod, _control = patched
    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="bear", address="OLD:ADDR"))

    async def fake_scan_ble(**_kwargs: Any) -> list[Any]:
        return [_candidate("ble", "NEW:ADDR")]

    monkeypatch.setattr(client_mod, "scan_ble", fake_scan_ble)
    enroll = _FakeEnrollment(BleIdentity(saved_name="bear", address="NEW:ADDR"))

    fresh = await PneumaticBearPokerClient.re_enroll("bear", enrollment={"ble": enroll}, store=store)

    assert enroll.calls == ["bear"]
    assert isinstance(fresh, BleIdentity)
    assert fresh.address == "NEW:ADDR"
    # The saved record was overwritten in place.
    assert store.load("bear").address == "NEW:ADDR"


async def test_connect_on_stale_wired_to_re_enroll(
    patched: tuple[Any, dict[str, bool]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a stale bond recovers by wiring on_stale to re_enroll."""
    from kandra_runtime import BleIdentity
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId

    client_mod, control = patched
    store = _make_store(tmp_path)
    store.save(BleIdentity(saved_name="bear", address="OLD:ADDR"))
    control["stale_ble"] = True

    async def fake_scan_ble(**_kwargs: Any) -> list[Any]:
        return [_candidate("ble", "NEW:ADDR")]

    monkeypatch.setattr(client_mod, "scan_ble", fake_scan_ble)
    enroll = _FakeEnrollment(BleIdentity(saved_name="bear", address="NEW:ADDR"))

    async def on_stale(name: str) -> Any:
        control["stale_ble"] = False
        return await PneumaticBearPokerClient.re_enroll(name, enrollment={"ble": enroll}, store=store)

    client = await PneumaticBearPokerClient.connect("bear", store=store, on_stale=on_stale)
    try:
        assert set(client._transports.keys()) == {TransportId.BLE}
        assert store.load("bear").address == "NEW:ADDR"
    finally:
        await client.aclose()
