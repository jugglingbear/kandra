"""Containerized end-to-end integration tests for the generated SDK.

Spins up the Pneumatic Bear Poker firmware simulator (Flask + waitress)
inside a Docker container via `testcontainers`, then walks the full
M7 lifecycle against it using **real** network components:

- real `HttpScanner` (parallel aiohttp probes against `/.well-known/...`)
- real `HttpEnrollment` (POST `/v1/auth/login`, extract token)
- real `PlatformDirsJsonStore` (tmp-scoped on-disk JSON)
- real `HttpTransport` (aiohttp `ClientSession` round-trip)
- real generated `PneumaticBearPokerClient.connect(saved_name=...)`
- real codec + interpreter + `Result` envelope

The only injected fakery is restricting the manifest's static
`base_urls` to the container's mapped port; everything else runs through
production code paths.

Marked `@pytest.mark.integration` so the fast unit suite (`pytest`) does
not require Docker. Run explicitly with::

    poetry run pytest -m integration -v        # or `make test-integration`
"""

from __future__ import annotations

import sys
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

try:
    from testcontainers.core.container import DockerContainer
except ImportError as exc:  # pragma: no cover - exercised only when the extra is missing
    pytest.skip(f"testcontainers not installed: {exc}", allow_module_level=True)

from kandra.generator import build_sdk  # noqa: E402  # must follow testcontainers skip-guard

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "pneumatic_bear_poker"
EXAMPLE_MANIFEST = EXAMPLE_DIR / "manifest.yaml"
EXAMPLE_SRC = EXAMPLE_DIR / "src"
FIRMWARE_SIM_DIR = EXAMPLE_DIR / "firmware_sim"

IMAGE_TAG = "pneumatic-bear-poker-sim:integration-test"
SAVED_NAME = "containerized-bear"


# ---------------------------------------------------------------------------
# Session-scoped fixtures (build image, start container, build SDK once).
# ---------------------------------------------------------------------------


def _build_image() -> None:
    """Build the firmware-sim image if it is not already present."""
    import docker  # testcontainers' bundled dep

    client = docker.from_env()
    try:
        client.images.get(IMAGE_TAG)
        return
    except docker.errors.ImageNotFound:
        pass
    client.images.build(path=str(FIRMWARE_SIM_DIR), tag=IMAGE_TAG, rm=True)


def _await_well_known(base_url: str, *, deadline_seconds: float = 30.0) -> None:
    """Poll the discovery probe until it returns 200 or the deadline elapses."""
    url = base_url.rstrip("/") + "/.well-known/pneumatic-bear-poker"
    deadline = time.monotonic() + deadline_seconds
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:  # localhost container
                if resp.status == 200:
                    return
        except Exception as exc:  # any error => retry
            last_exc = exc
            time.sleep(0.2)
    raise RuntimeError(f"firmware sim did not become ready at {url}: {last_exc!r}")


@pytest.fixture(scope="session")
def firmware_sim_url() -> Iterator[str]:
    """Start the firmware sim container once per session; yield `http://host:port`."""
    _build_image()
    container = DockerContainer(IMAGE_TAG).with_exposed_ports(8080)
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(8080)
        base = f"http://{host}:{port}"
        _await_well_known(base)
        yield base
    finally:
        container.stop()


@pytest.fixture(scope="session")
def sdk_on_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Build the example SDK once per session; expose it on ``sys.path``."""
    out = tmp_path_factory.mktemp("sdk")
    result = build_sdk(EXAMPLE_MANIFEST, output_root=out)
    added = [str(EXAMPLE_SRC), str(out)]
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


@pytest.fixture(scope="session")
def store_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Per-session on-disk identity store directory."""
    return tmp_path_factory.mktemp("store")


@pytest.fixture(scope="session")
def patched_base_urls(
    sdk_on_path: Path,  # - must build SDK before importing scanners
    firmware_sim_url: str,
) -> Iterator[str]:
    """Rebind the generated `_HTTP_BASE_URLS` tuple to the container URL."""
    import pneumatic_bear_poker_sdk.scanners as scanners_mod

    original = scanners_mod._HTTP_BASE_URLS
    scanners_mod._HTTP_BASE_URLS = (firmware_sim_url,)
    try:
        yield firmware_sim_url
    finally:
        scanners_mod._HTTP_BASE_URLS = original


# ---------------------------------------------------------------------------
# Lifecycle tests -- each phase runs in order, sharing session state.
# ---------------------------------------------------------------------------


async def test_step1_discovery_finds_firmware_sim(patched_base_urls: str) -> None:
    """`scan_http()` probes `/.well-known/...` and matches `Server: PneumaticBearPoker/*`."""
    from pneumatic_bear_poker_sdk import scan_http

    candidates = await scan_http(timeout=5.0)
    assert len(candidates) == 1, f"expected exactly one discovery hit, got {candidates}"
    candidate = candidates[0]
    assert candidate.address == patched_base_urls
    assert (candidate.advertised_name or "").startswith(
        "PneumaticBearPoker"
    ), f"Server header did not match prefix: {candidate.advertised_name!r}"


async def test_step2_enrollment_obtains_bearer_token(
    patched_base_urls: str,  # - ensures rebind active
    store_dir: Path,
) -> None:
    """`HttpEnrollment` POSTs `/v1/auth/login`, extracts the bearer token, then saves to disk."""
    from kandra_runtime import HttpEnrollment, PlatformDirsJsonStore
    from pneumatic_bear_poker_sdk import scan_http

    candidate = (await scan_http(timeout=5.0))[0]
    identity = await HttpEnrollment(login_path="/v1/auth/login").enroll(candidate, saved_name=SAVED_NAME)
    assert identity.transport == "http"
    assert identity.auth_token, "login response did not produce a bearer token"

    store = PlatformDirsJsonStore(app_name="pneumatic_bear_poker_sdk", directory=store_dir)
    store.save(identity)


async def test_step3_saved_identity_is_listable(store_dir: Path) -> None:
    """`Client.list_saved(store=...)` returns the just-enrolled saved name."""
    from kandra_runtime import PlatformDirsJsonStore
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = PlatformDirsJsonStore(app_name="pneumatic_bear_poker_sdk", directory=store_dir)
    assert PneumaticBearPokerClient.list_saved(store=store) == [SAVED_NAME]


# ---------------------------------------------------------------------------
# Command dispatch tests -- the arm/power state machine, plus logs.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    patched_base_urls: str,  # - ensures rebind active
    store_dir: Path,
) -> AsyncIterator[Any]:
    """Real `PneumaticBearPokerClient` connected via aiohttp `HttpTransport`."""
    from kandra_runtime import PlatformDirsJsonStore
    from pneumatic_bear_poker_sdk import PneumaticBearPokerClient

    store = PlatformDirsJsonStore(app_name="pneumatic_bear_poker_sdk", directory=store_dir)
    async with await PneumaticBearPokerClient.connect(SAVED_NAME, store=store) as c:
        yield c


def _assert_accepted(result: Any, *, attr: str, expected: Any) -> None:
    assert (
        result is not None and result.accepted
    ), f"command not accepted: classification={getattr(result, 'classification', None)}"
    assert result.data is not None, "accepted result missing data payload"
    actual = getattr(result.data, attr)
    assert actual == expected, f"{attr}: expected {expected!r}, got {actual!r}"


def _assert_rejected(result: Any) -> None:
    assert (
        result is not None and result.classification.name == "REJECTED"
    ), f"expected REJECTED, got classification={getattr(result, 'classification', None)}"


# The sim keeps power + arm state for the whole session, so each state-machine test starts and ends in the
# initial OFF / retracted state. power.off is fire-and-forget in the manifest, so it returns None.


async def test_fsm_arm_commands_rejected_while_off(client: Any) -> None:
    """deploy / retract are refused (HTTP 409 -> REJECTED) until the unit is powered."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest, RetractRequest

    _assert_rejected(await client.poker.deploy(DeployRequest(pressure_psi=42)))
    _assert_rejected(await client.poker.retract(RetractRequest()))


async def test_fsm_deploy_retract_cycle(client: Any) -> None:
    """Power on, extend, refuse a second extend, retract (twice -- the repeat is a no-op), power off."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest, RetractRequest
    from devices.pneumatic_bear_poker.handlers.power import PowerOffRequest, PowerOnRequest

    _assert_accepted(await client.power.on(PowerOnRequest()), attr="powered", expected=True)
    _assert_accepted(await client.poker.deploy(DeployRequest(pressure_psi=42)), attr="delivered_psi", expected=42)
    _assert_rejected(await client.poker.deploy(DeployRequest(pressure_psi=42)))
    _assert_accepted(await client.poker.retract(RetractRequest()), attr="retracted", expected=True)
    _assert_accepted(await client.poker.retract(RetractRequest()), attr="retracted", expected=True)
    assert await client.power.off(PowerOffRequest(drain_pressure=True)) is None


async def test_fsm_power_off_leaves_arm_extended(client: Any) -> None:
    """Cutting power never moves the arm: it stays extended until powered and retracted."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest, RetractRequest
    from devices.pneumatic_bear_poker.handlers.power import PowerOffRequest, PowerOnRequest

    _assert_accepted(await client.power.on(PowerOnRequest()), attr="powered", expected=True)
    _assert_accepted(await client.poker.deploy(DeployRequest(pressure_psi=7)), attr="delivered_psi", expected=7)
    assert await client.power.off(PowerOffRequest(drain_pressure=False)) is None
    _assert_rejected(await client.poker.retract(RetractRequest()))

    _assert_accepted(await client.power.on(PowerOnRequest()), attr="powered", expected=True)
    _assert_rejected(await client.poker.deploy(DeployRequest(pressure_psi=7)))  # still extended
    _assert_accepted(await client.poker.retract(RetractRequest()), attr="retracted", expected=True)
    assert await client.power.off(PowerOffRequest()) is None


async def test_cmd_logs_download_returns_requested_window(client: Any) -> None:
    """GET /v1/logs (query-string-encoded) honours `max_lines` and increments sequence."""
    from devices.pneumatic_bear_poker.handlers.diagnostics import DownloadLogsRequest

    result = await client.logs.download(DownloadLogsRequest(max_lines=3))
    assert result is not None and result.accepted
    assert result.data is not None
    assert len(result.data.lines) == 3, f"expected 3 lines, got {result.data.lines!r}"
    assert result.data.next_sequence == len(result.data.lines)


# ---------------------------------------------------------------------------
# Attribute + event dispatch tests.
# ---------------------------------------------------------------------------


async def test_attr_poke_intensity_read_write(client: Any) -> None:
    """GET/PUT /v1/settings/poke_intensity round-trips the attribute value."""
    from devices.pneumatic_bear_poker.handlers.settings import PokeIntensity

    read = await client.settings.poke_intensity.read()
    assert read is not None and read.accepted

    wrote = await client.settings.poke_intensity.write(PokeIntensity(level=9))
    assert wrote is not None and wrote.accepted
    assert wrote.data == PokeIntensity(level=9)

    reread = await client.settings.poke_intensity.read()
    assert reread.data == PokeIntensity(level=9)


async def test_attr_poke_intensity_subscribe_streams_updates(client: Any) -> None:
    """SSE /v1/settings/poke_intensity/events yields a finite stream of decoded values."""
    results = [r async for r in client.settings.poke_intensity.subscribe()]
    assert len(results) == 3
    assert all(r.accepted and r.data is not None for r in results)


async def test_event_bear_stirred_subscribe_streams_emissions(client: Any) -> None:
    """SSE /v1/alerts/bear_stirred/events yields the device's stirred emissions."""
    results = [r async for r in client.alerts.bear_stirred.subscribe()]
    assert all(r.accepted and r.data is not None for r in results)
    assert [r.data.magnitude for r in results] == [17, 42, 99]
