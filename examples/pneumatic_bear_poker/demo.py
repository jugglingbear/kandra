#!/usr/bin/env python3
"""End-to-end lifecycle demo for the generated `pneumatic_bear_poker_sdk`.

Walks through the four phases a real caller follows after running
``kandra build examples/pneumatic_bear_poker/manifest.yaml``:

    1. DISCOVER -- find a device in range
    2. ENROLL   -- bond / authenticate / capture credentials
    3. SAVE     -- persist the resulting identity so we can skip
                   discovery + enrollment next time
    4. CONNECT  -- reopen the device by saved name and send a command

Each phase is one line of code in the generated SDK. The narrative
comments are the point: this file doubles as the user-facing reference
for "what does kandra actually buy me?"

**Expects a Pneumatic Bear Poker reachable over HTTP.** By default it looks
for one at ``http://localhost:8080`` -- start the Flask firmware simulator
under ``examples/pneumatic_bear_poker/firmware_sim`` and this runs out of the
box. Point at real hardware by setting ``BEAR_POKER_URL``. The integration
test ``tests/test_example_end_to_end.py`` exercises the same flow with
in-process fakes so CI needs no device.
"""

from __future__ import annotations

import asyncio
import os

from devices.pneumatic_bear_poker.handlers.poker import DeployRequest
from kandra_runtime import (
    HttpEnrollment,
    PlatformDirsJsonStore,
)
from pneumatic_bear_poker_sdk import (
    PneumaticBearPokerClient,
    scan_http,
)

SAVED_NAME = "kitchen-bear"

# Where to look for the device. Defaults to the local firmware sim; override for
# real hardware, e.g. BEAR_POKER_URL=http://192.168.1.1:8080.
BEAR_POKER_URL = os.environ.get("BEAR_POKER_URL", "http://localhost:8080")


async def first_run() -> None:
    """Discover + enroll + save -- runs once per device."""
    # ---- (1) DISCOVER ------------------------------------------------------
    # `scan_http` uses the manifest's baked-in probe_path + matcher; we pass
    # `base_urls` to aim it at our sim (or real hardware) rather than the
    # manifest's default addresses.
    http_candidates = await scan_http(timeout=5.0, base_urls=[BEAR_POKER_URL])
    if not http_candidates:
        raise SystemExit(f"no Pneumatic Bear Poker reachable at {BEAR_POKER_URL}")
    http_candidate = http_candidates[0]
    print(f"found HTTP: {http_candidate.address}")

    # ---- (2) ENROLL --------------------------------------------------------
    # `HttpEnrollment` POSTs a login form and captures the bearer token,
    # returning a rich identity record the SDK can later replay with zero
    # user input.
    http_identity = await HttpEnrollment(
        login_path="/v1/auth/login",
    ).enroll(http_candidate, saved_name=SAVED_NAME)

    # ---- (3) SAVE ----------------------------------------------------------
    # `PlatformDirsJsonStore` defaults to the OS-appropriate per-user
    # config dir (`~/Library/Application Support/pneumatic_bear_poker_sdk/`
    # on macOS, `~/.config/...` on Linux).
    store = PlatformDirsJsonStore(app_name="pneumatic_bear_poker_sdk")
    store.save(http_identity)
    print(f"enrolled {SAVED_NAME!r}; saved identity to {store}")


async def subsequent_run() -> None:
    """Reopen the device by saved name and drive a command."""
    # `connect()` reads the saved identity, calls `from_identity` on
    # every matching transport, opens each one, and hands back an async
    # context manager that closes them on exit.
    async with await PneumaticBearPokerClient.connect(SAVED_NAME) as client:
        result = await client.poker.deploy(DeployRequest(pressure_psi=42))

    assert result is not None
    if not result.accepted:
        raise SystemExit(f"deploy rejected: {result.classification.name}")
    assert result.data is not None
    print(f"bear poked: delivered_psi={result.data.delivered_psi}")


async def main() -> None:
    """Run enrollment once if no identity is saved, then drive a command."""
    if SAVED_NAME not in PneumaticBearPokerClient.list_saved():
        await first_run()
    await subsequent_run()


if __name__ == "__main__":
    asyncio.run(main())
