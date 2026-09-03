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
    assert (candidate.advertised_name or "").startswith("PneumaticBearPoker"), (
        f"Server header did not match prefix: {candidate.advertised_name!r}"
    )


async def test_step2_enrollment_obtains_bearer_token(
    patched_base_urls: str,  # - ensures rebind active
    store_dir: Path,
) -> None:
    """`HttpEnrollment` POSTs `/v1/auth/login`, extracts the bearer token, then saves to disk."""
    from kandra_runtime import HttpEnrollment, PlatformDirsJsonStore
    from pneumatic_bear_poker_sdk import scan_http

    candidate = (await scan_http(timeout=5.0))[0]
    identity = await HttpEnrollment(login_path="/v1/auth/login").enroll(
        candidate, saved_name=SAVED_NAME
    )
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
# Command dispatch tests -- one per manifest command (and one extra branch).
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
    assert result is not None and result.accepted, (
        f"command not accepted: classification={getattr(result, 'classification', None)}"
    )
    assert result.data is not None, "accepted result missing data payload"
    actual = getattr(result.data, attr)
    assert actual == expected, f"{attr}: expected {expected!r}, got {actual!r}"


async def test_cmd_poker_deploy_echoes_pressure(client: Any) -> None:
    """POST /v1/poker/deploy round-trips `pressure_psi` → `delivered_psi`."""
    from devices.pneumatic_bear_poker.handlers.poker import DeployRequest

    result = await client.poker.deploy(DeployRequest(pressure_psi=42))
    _assert_accepted(result, attr="delivered_psi", expected=42)


async def test_cmd_safety_emergency_retract_returns_true(client: Any) -> None:
    """POST /v1/safety/emergency_retract reports `retracted=True`."""
    from devices.pneumatic_bear_poker.handlers.safety import EmergencyRetractRequest

    result = await client.safety.emergency_retract(EmergencyRetractRequest())
    _assert_accepted(result, attr="retracted", expected=True)


async def test_cmd_power_on_returns_powered_true(client: Any) -> None:
    """POST /v1/power/on flips `powered=True`."""
    from devices.pneumatic_bear_poker.handlers.power import PowerOnRequest

    result = await client.power.on(PowerOnRequest())
    _assert_accepted(result, attr="powered", expected=True)


async def test_cmd_power_off_drain_drops_residual_to_zero(client: Any) -> None:
    """POST /v1/power/off with `drain_pressure=True` leaves no residual psi."""
    from devices.pneumatic_bear_poker.handlers.power import PowerOffRequest

    result = await client.power.off(PowerOffRequest(drain_pressure=True))
    _assert_accepted(result, attr="powered", expected=False)
    assert result.data.residual_psi == 0


async def test_cmd_power_off_no_drain_retains_residual_psi(client: Any) -> None:
    """POST /v1/power/off with `drain_pressure=False` keeps a residual ~12 psi."""
    from devices.pneumatic_bear_poker.handlers.power import PowerOffRequest

    result = await client.power.off(PowerOffRequest(drain_pressure=False))
    _assert_accepted(result, attr="powered", expected=False)
    assert result.data.residual_psi > 0, "expected residual pressure when drain_pressure=False"


async def test_cmd_logs_download_returns_requested_window(client: Any) -> None:
    """GET /v1/logs (query-string-encoded) honours `max_lines` and increments sequence."""
    from devices.pneumatic_bear_poker.handlers.diagnostics import DownloadLogsRequest

    result = await client.logs.download(DownloadLogsRequest(max_lines=3))
    assert result is not None and result.accepted
    assert result.data is not None
    assert len(result.data.lines) == 3, f"expected 3 lines, got {result.data.lines!r}"
    assert result.data.next_sequence == len(result.data.lines)
