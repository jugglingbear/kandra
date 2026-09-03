"""Tests for the Enrollment protocol and BLE/HTTP adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import pytest
from aiohttp import web
from kandra_runtime import (
    BleEnrollment,
    BleIdentity,
    Candidate,
    EnrollmentError,
    HttpEnrollment,
    HttpIdentity,
)

# ---------------------------------------------------------------------------
# BleEnrollment: stub out BleTransport so no actual bleak machinery runs.
# ---------------------------------------------------------------------------


class _FakeBleTransport:
    """Stand-in for BleTransport: records open/close instead of using bleak."""

    instances: ClassVar[list[_FakeBleTransport]] = []

    def __init__(self, address: str, channels: Mapping[str, tuple[str, str]]) -> None:
        self.address = address
        self.channels = channels
        self.opened = False
        self.closed = False
        type(self).instances.append(self)

    async def __aenter__(self) -> _FakeBleTransport:
        self.opened = True
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.closed = True


async def test_ble_enrollment_opens_transport_and_returns_identity() -> None:
    _FakeBleTransport.instances.clear()
    enrollment = BleEnrollment(
        transport_factory=_FakeBleTransport,
        probe_channel=("0000ffff-0000-1000-8000-00805f9b34fb", "0000fffe-0000-1000-8000-00805f9b34fb"),
    )
    candidate = Candidate(
        transport="ble", address="AA:BB:CC:DD:EE:FF", advertised_name="PBP-001"
    )
    ident = await enrollment.enroll(candidate, saved_name="poker")
    assert isinstance(ident, BleIdentity)
    assert ident.saved_name == "poker"
    assert ident.address == "AA:BB:CC:DD:EE:FF"
    assert ident.advertised_name == "PBP-001"
    # The fake transport was actually opened (and the probe channel passed through).
    assert len(_FakeBleTransport.instances) == 1
    inst = _FakeBleTransport.instances[0]
    assert inst.opened and inst.closed
    assert "probe" in inst.channels


async def test_ble_enrollment_rejects_wrong_transport() -> None:
    enrollment = BleEnrollment(transport_factory=_FakeBleTransport)
    bad = Candidate(transport="http", address="http://x")
    with pytest.raises(EnrollmentError, match="non-BLE"):
        await enrollment.enroll(bad, saved_name="poker")


async def test_ble_enrollment_propagates_open_failure() -> None:
    class _BoomTransport(_FakeBleTransport):
        async def __aenter__(self) -> _BoomTransport:
            raise RuntimeError("bond failed")

    enrollment = BleEnrollment(transport_factory=_BoomTransport)
    candidate = Candidate(transport="ble", address="AA:BB:CC:DD:EE:FF")
    with pytest.raises(EnrollmentError, match="bond"):
        await enrollment.enroll(candidate, saved_name="poker")


# ---------------------------------------------------------------------------
# HttpEnrollment against an aiohttp test server.
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_server():  # type: ignore[no-untyped-def]
    seen_payloads: list[dict[str, Any]] = []

    async def login(request: web.Request) -> web.Response:
        payload = await request.json()
        seen_payloads.append(payload)
        if payload.get("username") == "user" and payload.get("password") == "pw":
            return web.json_response({"token": "tok-abc"})
        return web.json_response({"error": "denied"}, status=401)

    async def login_alt(_request: web.Request) -> web.Response:
        return web.json_response({"access_token": "alt-token"})

    app = web.Application()
    app.router.add_post("/login", login)
    app.router.add_post("/login_alt", login_alt)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    assert sockets is not None
    port = sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        yield base, seen_payloads
    finally:
        await runner.cleanup()


async def test_http_enrollment_no_login_returns_token_less_identity(auth_server) -> None:  # type: ignore[no-untyped-def]
    base, _seen = auth_server
    enrollment = HttpEnrollment()  # no login_path -> no-op handshake
    candidate = Candidate(transport="http", address=base)
    ident = await enrollment.enroll(candidate, saved_name="cloud")
    assert isinstance(ident, HttpIdentity)
    assert ident.base_url == base
    assert ident.auth_token is None


async def test_http_enrollment_with_login_extracts_token(auth_server) -> None:  # type: ignore[no-untyped-def]
    base, seen = auth_server
    enrollment = HttpEnrollment(
        login_path="/login",
        login_payload=lambda _c: {"username": "user", "password": "pw"},
    )
    candidate = Candidate(transport="http", address=base)
    ident = await enrollment.enroll(candidate, saved_name="cloud")
    assert isinstance(ident, HttpIdentity)
    assert ident.auth_token == "tok-abc"
    assert seen == [{"username": "user", "password": "pw"}]


async def test_http_enrollment_with_async_login_payload(auth_server) -> None:  # type: ignore[no-untyped-def]
    base, _seen = auth_server

    async def async_payload(_c: Candidate) -> dict[str, str]:
        return {"username": "user", "password": "pw"}

    enrollment = HttpEnrollment(login_path="/login", login_payload=async_payload)
    candidate = Candidate(transport="http", address=base)
    ident = await enrollment.enroll(candidate, saved_name="cloud")
    assert isinstance(ident, HttpIdentity)
    assert ident.auth_token == "tok-abc"


async def test_http_enrollment_with_custom_extractor(auth_server) -> None:  # type: ignore[no-untyped-def]
    base, _ = auth_server
    enrollment = HttpEnrollment(
        login_path="/login_alt",
        login_payload=lambda _c: {},
        token_extractor=lambda body: str(body["access_token"]),
    )
    candidate = Candidate(transport="http", address=base)
    ident = await enrollment.enroll(candidate, saved_name="cloud")
    assert isinstance(ident, HttpIdentity)
    assert ident.auth_token == "alt-token"


async def test_http_enrollment_login_failure_raises(auth_server) -> None:  # type: ignore[no-untyped-def]
    base, _ = auth_server
    enrollment = HttpEnrollment(
        login_path="/login",
        login_payload=lambda _c: {"username": "wrong", "password": "wrong"},
    )
    candidate = Candidate(transport="http", address=base)
    with pytest.raises(EnrollmentError, match="login"):
        await enrollment.enroll(candidate, saved_name="cloud")


async def test_http_enrollment_rejects_wrong_transport() -> None:
    enrollment = HttpEnrollment()
    bad = Candidate(transport="ble", address="AA:BB")
    with pytest.raises(EnrollmentError, match="non-HTTP"):
        await enrollment.enroll(bad, saved_name="cloud")


async def test_http_enrollment_payload_callable_returning_non_mapping(auth_server) -> None:  # type: ignore[no-untyped-def]
    base, _ = auth_server
    enrollment = HttpEnrollment(
        login_path="/login",
        login_payload=lambda _c: "not a dict",  # type: ignore[arg-type,return-value]
    )
    candidate = Candidate(transport="http", address=base)
    with pytest.raises(EnrollmentError, match="non-mapping"):
        await enrollment.enroll(candidate, saved_name="cloud")


# ---------------------------------------------------------------------------
# Transport.from_identity classmethods.
# ---------------------------------------------------------------------------


def test_http_transport_from_identity_sets_bearer() -> None:
    from kandra_runtime.http_transport import HttpTransport

    ident = HttpIdentity(saved_name="cloud", base_url="http://x", auth_token="tok-abc")
    transport = HttpTransport.from_identity(ident)
    assert "Authorization" in transport._default_headers
    assert transport._default_headers["Authorization"] == "Bearer tok-abc"
    assert transport._base_url.rstrip("/") == "http://x"


def test_http_transport_from_identity_user_headers_override() -> None:
    from kandra_runtime.http_transport import HttpTransport

    ident = HttpIdentity(saved_name="cloud", base_url="http://x", auth_token="tok-abc")
    transport = HttpTransport.from_identity(
        ident, default_headers={"Authorization": "Bearer override"}
    )
    assert transport._default_headers["Authorization"] == "Bearer override"


def test_http_transport_from_identity_wrong_type_raises() -> None:
    from kandra_runtime.http_transport import HttpTransport

    with pytest.raises(TypeError, match="HttpIdentity"):
        HttpTransport.from_identity(BleIdentity(saved_name="x", address="AA:BB"))


def test_ble_transport_from_identity_wrong_type_raises() -> None:
    from kandra_runtime.ble_transport import BleTransport

    with pytest.raises(TypeError, match="BleIdentity"):
        BleTransport.from_identity(
            HttpIdentity(saved_name="x", base_url="http://x"),
            channels={},
        )
