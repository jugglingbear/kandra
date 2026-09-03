"""End-to-end integration test for the `pneumatic_bear_poker` example (M7 finale).

Demonstrates the full lifecycle a real user would follow:

1. **Discover** a device with ``scan_ble()`` (manifest's discovery block →
   generated :func:`scan_ble`).
2. **Enroll** it: pick a candidate, build an identity record.
3. **Save** the identity in a :class:`PlatformDirsJsonStore`.
4. **Reconnect** later via :meth:`Client.connect(saved_name=...)`.
5. **Dispatch** a real command via the saved-and-reopened client.

Hardware is faked at the lowest reasonable seam so the test exercises the
generated package's *integration*, not just per-module units:

- ``scanners.BleScanner`` is monkey-patched to a class that yields a single
  fake candidate via the same ``Scanner`` protocol the real adapter uses.
- ``client.BleTransport`` / ``client.HttpTransport`` are monkey-patched so
  ``from_identity`` returns an in-process transport that drives the
  command pipeline end-to-end (request/response round-trip through codec
  + interpreter + result envelope).
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from kandra.generator import build_sdk
from kandra_runtime import Candidate, HttpRequest, HttpResponse

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "pneumatic_bear_poker"
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


class _FakeBleScanner:
    """Stand-in for ``kandra_runtime.BleScanner`` that yields one candidate."""

    async def scan(
        self,
        *,
        matcher: Any = None,
        timeout: float | None = None,
    ) -> AsyncIterator[Candidate]:
        candidate = Candidate(
            transport="ble",
            address="AA:BB:CC:DD:EE:FF",
            advertised_name="BearPoker-1234",
            metadata={
                "service_uuids": ("b5f90000-aa8d-11e3-9046-0002a5d5c51b",),
                "manufacturer_data": {},
            },
        )
        if matcher is None or matcher(candidate):
            yield candidate


class _FakeHttpTransport:
    """In-process transport that drives the HTTP command pipeline end-to-end.

    Implements just enough of ``Transport[HttpRequest, HttpResponse]`` for
    the generated client to dispatch a command through the codec + interpreter.
    The constructor mirrors ``HttpTransport.from_identity``'s signature so the
    monkey-patch can be a drop-in replacement.
    """

    def __init__(self, identity: Any) -> None:
        self._identity = identity
        self.opened = False
        self.closed = False

    @classmethod
    def from_identity(cls, identity: Any) -> _FakeHttpTransport:
        return cls(identity)

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    @property
    def is_open(self) -> bool:
        return self.opened and not self.closed

    async def request(self, envelope: HttpRequest) -> HttpResponse:
        # Echo the request body's `pressure_psi` field as `delivered_psi`,
        # mirroring what a real Pneumatic Bear Poker firmware would do.
        body = json.loads((envelope.body or b"{}").decode("utf-8"))
        payload = json.dumps({"delivered_psi": body["pressure_psi"]}).encode("utf-8")
        return HttpResponse(status=200, body=payload)


async def test_full_lifecycle_discover_enroll_save_reconnect_dispatch(
    sdk_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Walk a user through the full M7 happy path against the generated SDK."""
    # ---- imports happen *after* the SDK build fixture wires sys.path -------
    import pneumatic_bear_poker_sdk.client as client_mod
    import pneumatic_bear_poker_sdk.scanners as scanners_mod
    from devices.pneumatic_bear_poker.handlers.poker import (
        DeployRequest,
        DeployResponse,
    )
    from kandra_runtime import (
        CompositeIdentity,
        HttpIdentity,
        PlatformDirsJsonStore,
    )
    from pneumatic_bear_poker_sdk import (
        PneumaticBearPokerClient,
        TransportId,
        scan_ble,
    )

    # ---- (1) DISCOVER ------------------------------------------------------
    monkeypatch.setattr(scanners_mod, "BleScanner", _FakeBleScanner)
    candidates = await scan_ble(timeout=1.0)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.address == "AA:BB:CC:DD:EE:FF"
    assert candidate.advertised_name == "BearPoker-1234"

    # ---- (2) ENROLL: synthesize a composite identity from the candidate ----
    # In a real flow this would run BleEnrollment + HttpEnrollment adapters;
    # here we fast-forward since the unit tests already cover enrollment.
    saved_name = "kitchen-bear"
    identity = CompositeIdentity(
        saved_name=saved_name,
        components={
            "api": HttpIdentity(
                saved_name=saved_name,
                base_url="http://192.168.1.1:8080",
            ),
        },
    )

    # ---- (3) SAVE the identity in a tmp-scoped store -----------------------
    store = PlatformDirsJsonStore(
        app_name="pneumatic_bear_poker_sdk",
        directory=tmp_path / "store",
    )
    store.save(identity)
    assert PneumaticBearPokerClient.list_saved(store=store) == [saved_name]

    # ---- (4) RECONNECT via the saved name ----------------------------------
    # Monkey-patch the client module's transport symbols so `from_identity`
    # returns an in-process fake instead of opening a real socket.
    monkeypatch.setattr(client_mod, "HttpTransport", _FakeHttpTransport)
    # Re-bind the factory table so the lambda picks up the patched symbol.
    monkeypatch.setattr(
        client_mod,
        "_TRANSPORT_FACTORIES",
        (
            (
                TransportId.HTTP,
                "http",
                lambda ident: _FakeHttpTransport.from_identity(ident),
            ),
        ),
    )

    async with await PneumaticBearPokerClient.connect(saved_name, store=store) as client:
        # ---- (5) DISPATCH a real command through the generated facade -----
        result = await client.poker.deploy(DeployRequest(pressure_psi=42))

    assert result is not None
    assert result.accepted
    assert result.data == DeployResponse(delivered_psi=42)
    assert result.extra == {"http_status": 200}
