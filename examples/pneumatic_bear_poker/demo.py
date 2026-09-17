#!/usr/bin/env python3
"""End-to-end lifecycle demo for the generated `pneumatic_bear_poker_sdk`.

Walks through the four phases a real caller follows after running
``kandra build examples/pneumatic_bear_poker/manifest.yaml``:

    1. DISCOVER -- find a device in range
    2. ENROLL   -- bond / authenticate / capture credentials
    3. SAVE     -- persist the resulting identity so we can skip discovery + enrollment next time
    4. CONNECT  -- reopen the device by saved name and send a command

Each phase is one line of code in the generated SDK. The narrative comments are the point: this file doubles as
the user-facing reference for "what does kandra actually buy me?"

**Expects a Pneumatic Bear Poker reachable over HTTP.** By default it looks for one at ``http://localhost:8080``
-- start the Flask firmware simulator under ``examples/pneumatic_bear_poker/firmware_sim`` and this runs out of
the box. Point elsewhere with ``--url`` (or the ``BEAR_POKER_URL`` env var), e.g. ``--url http://192.168.1.1:8080``
for real hardware; clear a stale saved identity with ``--reset``. The integration test
``tests/test_example_end_to_end.py`` exercises the same flow with in-process fakes so CI needs no device.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from devices.pneumatic_bear_poker.handlers.poker import DeployRequest
from kandra_runtime import PlatformDirsJsonStore
from pneumatic_bear_poker_sdk import (
    PneumaticBearPokerClient,
    http_enrollment,
    scan_http,
)

# The generated SDK's package name; also the identity store's app_name.
APP_NAME = "pneumatic_bear_poker_sdk"

# Human label for the saved identity -- like naming a saved Wi-Fi network. You could save several devices under
# different names ("grizzly", "polar", ...).
SAVED_NAME = "grizzly"

# Fallback device URL when neither --url nor BEAR_POKER_URL is given.
DEFAULT_URL = "http://localhost:8080"


def _log(step: str, message: str) -> None:
    """Print a labelled lifecycle-phase line so each step is visible as it runs."""
    print(f"[{step}] {message}")


async def first_run(url: str) -> None:
    """Discover + enroll + save -- runs once per device."""
    # ---- (1) DISCOVER ------------------------------------------------------
    # `scan_http` uses the manifest's baked-in probe_path + matcher; we pass `base_urls` to aim it at our sim (or
    # real hardware) rather than the manifest's default addresses.
    _log("DISCOVER", f"probing {url} for a Pneumatic Bear Poker ...")
    http_candidates = await scan_http(timeout=5.0, base_urls=[url])
    if not http_candidates:
        raise SystemExit(f"[DISCOVER] nothing reachable at {url} -- is the simulator running on that URL?")
    http_candidate = http_candidates[0]
    _log("DISCOVER", f"found device at {http_candidate.address}")

    # ---- (2) ENROLL --------------------------------------------------------
    # `http_enrollment()` is baked from the manifest's `enrollment:` block (login path + token field), so the
    # device's auth endpoint stays out of your code. It captures the bearer token into a replayable identity;
    # pass `login_payload=...` when a device needs credentials.
    _log("ENROLL", "running the manifest-declared login and capturing the bearer token ...")
    http_identity = await http_enrollment().enroll(http_candidate, saved_name=SAVED_NAME)
    _log("ENROLL", "credentials captured")

    # ---- (3) SAVE ----------------------------------------------------------
    # `PlatformDirsJsonStore` writes to the OS-appropriate per-user data dir
    # (`~/Library/Application Support/pneumatic_bear_poker_sdk/` on macOS, `~/.local/share/...` on Linux).
    store = PlatformDirsJsonStore(app_name=APP_NAME)
    store.save(http_identity)
    _log("SAVE", f"identity {SAVED_NAME!r} written to {store.path}")


async def subsequent_run(url: str) -> None:
    """Reopen the device by saved name and drive a command."""
    # `connect()` reads the saved identity, calls `from_identity` on every matching transport, opens each one,
    # and hands back an async context manager that closes them on exit.
    _log("CONNECT", f"reopening device from saved identity {SAVED_NAME!r} ...")
    async with await PneumaticBearPokerClient.connect(SAVED_NAME) as client:
        _log("DEPLOY", "sending poker.deploy(pressure_psi=42) ...")
        result = await client.poker.deploy(DeployRequest(pressure_psi=42))

    assert result is not None
    if not result.accepted:
        _log("DEPLOY", f"rejected: {result.classification.name}")
        if result.classification.name == "TRANSPORT_FAILURE":
            # A saved identity pins the address captured at enrollment. If the device has since moved (e.g. a new
            # sim port), that address is dead -- indistinguishable from "device offline", so re-enroll to refresh it.
            reset_cmd = f"poetry run python examples/pneumatic_bear_poker/demo.py --reset --url {url}"
            print(f"       hint: saved identity {SAVED_NAME!r} may point at a stale address (expected {url}).")
            print(f"       hint: clear it and re-enroll with:  {reset_cmd}")
        raise SystemExit(1)
    assert result.data is not None
    _log("DEPLOY", f"accepted: bear poked, delivered_psi={result.data.delivered_psi}")


def _parse_args() -> argparse.Namespace:
    """Parse --url (device URL) and --reset (forget the saved identity first)."""
    parser = argparse.ArgumentParser(description="Pneumatic Bear Poker lifecycle demo.")
    parser.add_argument(
        "--url",
        default=os.environ.get("BEAR_POKER_URL", DEFAULT_URL),
        help=f"device base URL to discover/enroll against (env BEAR_POKER_URL, default {DEFAULT_URL})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=f"forget the saved {SAVED_NAME!r} identity before running (forces re-discovery + enrollment)",
    )
    return parser.parse_args()


async def main() -> None:
    """Run enrollment once if no identity is saved, then drive a command."""
    args = _parse_args()

    store = PlatformDirsJsonStore(app_name=APP_NAME)
    _log("STORE", f"identities file: {store.path}")
    if args.reset:
        store.delete(SAVED_NAME)
        _log("STORE", f"reset: forgot {SAVED_NAME!r}")

    saved = [identity.saved_name for identity in store.list_saved()]
    _log("STORE", f"currently saved: {saved or '(none)'}")

    if SAVED_NAME not in saved:
        _log("PHASE", f"no saved identity {SAVED_NAME!r} -- running discover + enroll")
        await first_run(args.url)
    else:
        _log("PHASE", f"reusing saved identity {SAVED_NAME!r} -- skipping discovery")

    await subsequent_run(args.url)


if __name__ == "__main__":
    asyncio.run(main())
