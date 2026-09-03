"""One-time device setup that produces a persistent :class:`~kandra_runtime.identity.Identity`.

The :class:`Enrollment` protocol covers the "I know this is the device
I want, now do whatever pairing / login / bonding handshake is needed
to make future ``connect()`` calls cheap" step. It takes a
:class:`~kandra_runtime.scanner.Candidate` (typically the result of
:class:`~kandra_runtime.scanner.Scanner.scan`) plus a user-chosen
``saved_name`` and returns the :class:`Identity` to persist in an
:class:`~kandra_runtime.identity.IdentityStore`.

Two ready-made adapters ship:

* :class:`BleEnrollment` — opens the BLE link once to confirm pairing
  succeeds (delegating bond storage to the OS), then returns a
  :class:`~kandra_runtime.identity.BleIdentity`.
* :class:`HttpEnrollment` — POSTs an optional login payload to a
  configurable path, captures the bearer token from the response, and
  returns an :class:`~kandra_runtime.identity.HttpIdentity`.

Device authors who need richer flows (camera Wi-Fi credential exchange,
multi-step pairing, captive-portal handshakes) subclass these or
provide their own :class:`Enrollment` implementation.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, runtime_checkable

import aiohttp

from kandra_runtime.ble_transport import BleTransport
from kandra_runtime.errors import KandraError, TransportError
from kandra_runtime.identity import BleIdentity, HttpIdentity, Identity
from kandra_runtime.scanner import Candidate


class EnrollmentError(KandraError):
    """Raised when enrollment fails (pairing rejected, login 401, etc.)."""


@runtime_checkable
class Enrollment(Protocol):
    """One-time device setup; produces the :class:`Identity` to persist.

    Implementations must accept any :class:`Candidate` whose ``transport``
    family they understand; they should raise :class:`EnrollmentError`
    for candidates from other families rather than returning ``None``.
    """

    async def enroll(self, candidate: Candidate, *, saved_name: str) -> Identity:
        """Run the enrollment handshake and return the persisted identity."""


# ---------------------------------------------------------------------------
# BLE adapter.
# ---------------------------------------------------------------------------


class _BleTransportLike(Protocol):
    """Minimal interface BleEnrollment needs (lets tests inject fakes)."""

    async def __aenter__(self) -> _BleTransportLike: ...

    async def __aexit__(self, *exc: Any) -> None: ...


BleTransportFactory = Callable[[str, Mapping[str, tuple[str, str]]], _BleTransportLike]


def _default_ble_transport_factory(
    address: str, channels: Mapping[str, tuple[str, str]]
) -> _BleTransportLike:
    return _BleTransportContextAdapter(BleTransport(address, channels=channels))


class _BleTransportContextAdapter:
    """Wraps the open/close BleTransport in an async-context-manager facade."""

    def __init__(self, transport: BleTransport) -> None:
        self._transport = transport

    async def __aenter__(self) -> _BleTransportContextAdapter:
        await self._transport.open()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        with contextlib.suppress(Exception):
            await self._transport.close()


class BleEnrollment(Enrollment):
    """BLE enrollment: opens the link once to drive OS bonding, then stores the address.

    On macOS / Linux, simply connecting to the peripheral with a paired
    characteristic write is enough for the OS to capture and persist
    the LTK. This adapter performs that connect+disconnect dance so the
    bond is in place before the user's first real
    ``Client.connect(saved_name=...)`` call.

    Pass ``probe_channel`` (a ``(write_uuid, notify_uuid)`` pair) when
    the device requires a paired characteristic touch to trigger
    bonding; leave it ``None`` to just connect and disconnect.

    Tests inject ``transport_factory`` to substitute the BLE layer.
    """

    def __init__(
        self,
        *,
        probe_channel: tuple[str, str] | None = None,
        transport_factory: BleTransportFactory | None = None,
    ) -> None:
        """Configure the BLE enrollment adapter (see class docstring)."""
        self._probe_channel = probe_channel
        self._transport_factory = transport_factory or _default_ble_transport_factory

    async def enroll(self, candidate: Candidate, *, saved_name: str) -> Identity:
        """Pair with ``candidate`` and return a :class:`BleIdentity`."""
        if candidate.transport != "ble":
            raise EnrollmentError(
                f"BleEnrollment cannot enroll non-BLE candidate "
                f"(transport={candidate.transport!r})"
            )
        channels: dict[str, tuple[str, str]] = (
            {"probe": self._probe_channel} if self._probe_channel is not None else {}
        )
        try:
            async with self._transport_factory(candidate.address, channels):
                pass
        except TransportError as exc:
            raise EnrollmentError(
                f"BleEnrollment: failed to bond with {candidate.address!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise EnrollmentError(
                f"BleEnrollment: failed to bond with {candidate.address!r}: {exc}"
            ) from exc
        return BleIdentity(
            saved_name=saved_name,
            address=candidate.address,
            advertised_name=candidate.advertised_name,
        )


# ---------------------------------------------------------------------------
# HTTP adapter.
# ---------------------------------------------------------------------------


LoginPayloadFactory = Callable[[Candidate], Awaitable[Mapping[str, Any]] | Mapping[str, Any]]
"""Optional callable producing the JSON body POSTed to ``login_path``."""

TokenExtractor = Callable[[Mapping[str, Any]], str | None]
"""Pulls the bearer token out of the login response JSON. Default reads ``token``."""


def _default_token_extractor(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("token") or payload.get("access_token")
    return value if isinstance(value, str) else None


class HttpEnrollment(Enrollment):
    """HTTP enrollment: optional login POST + token capture.

    When ``login_path`` is ``None``, enrollment is a no-op handshake —
    the candidate's address becomes the stored ``base_url`` and the
    identity carries no token. When ``login_path`` is set, the adapter
    POSTs the result of ``login_payload`` and runs ``token_extractor``
    against the response JSON to populate ``HttpIdentity.auth_token``.
    """

    def __init__(
        self,
        *,
        login_path: str | None = None,
        login_payload: LoginPayloadFactory | None = None,
        token_extractor: TokenExtractor = _default_token_extractor,
        session_factory: Callable[[], aiohttp.ClientSession] | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Configure the HTTP enrollment adapter (see class docstring)."""
        self._login_path = login_path
        self._login_payload = login_payload
        self._token_extractor = token_extractor
        self._timeout = timeout
        self._session_factory = session_factory or (
            lambda: aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
        )

    async def enroll(self, candidate: Candidate, *, saved_name: str) -> Identity:
        """Optionally log in to ``candidate.address`` and return an :class:`HttpIdentity`."""
        if candidate.transport != "http":
            raise EnrollmentError(
                f"HttpEnrollment cannot enroll non-HTTP candidate "
                f"(transport={candidate.transport!r})"
            )
        base_url = candidate.address.rstrip("/")
        token: str | None = None
        if self._login_path is not None:
            payload = await self._build_login_payload(candidate)
            session = self._session_factory()
            try:
                url = base_url + self._login_path
                try:
                    async with session.post(url, json=dict(payload)) as resp:
                        if resp.status >= 400:
                            raise EnrollmentError(
                                f"HttpEnrollment: login POST {url} returned {resp.status}"
                            )
                        body = await resp.json(content_type=None)
                except (TimeoutError, aiohttp.ClientError) as exc:
                    raise EnrollmentError(
                        f"HttpEnrollment: login POST to {url} failed: {exc}"
                    ) from exc
                if not isinstance(body, Mapping):
                    raise EnrollmentError(
                        f"HttpEnrollment: login response from {url} was not a JSON object"
                    )
                token = self._token_extractor(body)
            finally:
                await session.close()
        return HttpIdentity(
            saved_name=saved_name,
            base_url=base_url,
            auth_token=token,
        )

    async def _build_login_payload(self, candidate: Candidate) -> Mapping[str, Any]:
        if self._login_payload is None:
            return {}
        result = self._login_payload(candidate)
        if hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, Mapping):
            raise EnrollmentError(
                f"HttpEnrollment.login_payload returned non-mapping: {type(result).__name__}"
            )
        return result
