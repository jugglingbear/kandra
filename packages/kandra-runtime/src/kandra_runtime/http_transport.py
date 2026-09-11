r"""Built-in HTTP transport backed by ``aiohttp``.

Implements :class:`~kandra_runtime.transport.Transport`\\
``[HttpRequest, HttpResponse]`` so any command whose codec produces
:class:`HttpRequest` envelopes (the default for HTTP-family commands in
the generated SDK) can ride it.

Deployment configuration (base URL, default headers, TLS verify) is
*runtime-only* and lives on the transport constructor -- the manifest
deliberately does not bake in URLs because the same SDK runs against
many deployments.

See kandra.md section 8.
"""

from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING, Self
from urllib.parse import urljoin

import aiohttp

from kandra_runtime.errors import (
    IdentityStaleError,
    TransportError,
    TransportNotOpenError,
    TransportTimeoutError,
)
from kandra_runtime.http import HttpRequest, HttpResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Mapping


class HttpTransport:
    """Async HTTP transport backed by an :class:`aiohttp.ClientSession`.

    The transport owns its session by default and closes it on
    ``close()``. Pass ``session=...`` to inject a caller-managed session
    (useful for reusing a connection pool across multiple transports);
    in that mode ``close()`` is a no-op.

    Args:
        base_url: Scheme+host (e.g. ``https://10.5.5.9:443``). Joined
            with each :attr:`HttpRequest.path` via :func:`urllib.parse.urljoin`.
        default_headers: Headers applied to every request (per-request
            headers from :class:`HttpRequest` take precedence on conflict).
        timeout: Default per-request timeout in seconds. ``None`` disables
            the transport-level timeout (the per-command timeout still
            applies via :func:`kandra_runtime.dispatch`).
        verify_ssl: Forwarded to aiohttp's connector; default True.
        session: Optional pre-built session to reuse. When supplied, the
            transport will not close it.
    """

    def __init__(
        self,
        base_url: str,
        *,
        default_headers: Mapping[str, str] | None = None,
        timeout: float | None = 30.0,
        verify_ssl: bool = True,
        session: aiohttp.ClientSession | None = None,
        stale_statuses: Collection[int] | None = None,
    ) -> None:
        """Initialize the transport configuration. No I/O occurs here."""
        if not base_url:
            raise ValueError("base_url must be non-empty")
        # Normalize to end with '/' so urljoin treats it as a directory.
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"
        self._default_headers = dict(default_headers) if default_headers else {}
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._injected_session = session is not None
        self._session: aiohttp.ClientSession | None = session
        # Statuses treated as "stored credentials rejected" -> IdentityStaleError.
        # Pass an empty collection to opt out (let 4xx pass through to the codec).
        self._stale_statuses = frozenset({401, 403}) if stale_statuses is None else frozenset(stale_statuses)

    @classmethod
    def from_identity(
        cls,
        identity: object,
        *,
        default_headers: Mapping[str, str] | None = None,
        timeout: float | None = 30.0,
        verify_ssl: bool = True,
        session: aiohttp.ClientSession | None = None,
        stale_statuses: Collection[int] | None = None,
    ) -> HttpTransport:
        """Build a transport from a persisted :class:`~kandra_runtime.identity.HttpIdentity`.

        If the identity carries an ``auth_token``, it is added as a
        ``Authorization: Bearer <token>`` header on top of any
        ``default_headers`` supplied by the caller (caller-supplied
        ``Authorization`` wins on conflict).
        """
        from kandra_runtime.identity import HttpIdentity

        if not isinstance(identity, HttpIdentity):
            raise TypeError(f"HttpTransport.from_identity expected HttpIdentity, " f"got {type(identity).__name__}")
        headers: dict[str, str] = {}
        if identity.auth_token is not None:
            headers["Authorization"] = f"Bearer {identity.auth_token}"
        if default_headers:
            headers.update(default_headers)
        return cls(
            identity.base_url,
            default_headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
            session=session,
            stale_statuses=stale_statuses,
        )

    async def open(self) -> None:
        """Create the underlying ``aiohttp.ClientSession`` if not injected."""
        if self._session is not None:
            return
        connector = aiohttp.TCPConnector(ssl=self._verify_ssl)
        client_timeout = aiohttp.ClientTimeout(total=self._timeout) if self._timeout else aiohttp.ClientTimeout()
        self._session = aiohttp.ClientSession(connector=connector, timeout=client_timeout)

    async def close(self) -> None:
        """Close the underlying session (no-op when an injected session is in use)."""
        if self._session is None or self._injected_session:
            return
        await self._session.close()
        self._session = None

    @property
    def is_open(self) -> bool:
        """True after ``open()`` and before ``close()`` (and the session is alive)."""
        return self._session is not None and not self._session.closed

    async def __aenter__(self) -> Self:
        """Enter the async context manager, opening the session."""
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager, closing the session if owned."""
        await self.close()

    async def request(self, envelope: HttpRequest) -> HttpResponse:
        """Issue an HTTP request and return the response envelope.

        Raises:
            TransportNotOpenError: if called before :meth:`open`.
            IdentityStaleError: on a 401/403 (stored credentials rejected), unless
                ``stale_statuses`` was set empty to opt out.
            TransportTimeoutError: on aiohttp / asyncio timeout.
            TransportError: on connection or protocol failures.
        """
        if self._session is None or self._session.closed:
            raise TransportNotOpenError("HttpTransport.request() called before open()")
        url = urljoin(self._base_url, envelope.path.lstrip("/"))
        headers = {**self._default_headers, **envelope.headers}
        try:
            async with self._session.request(
                envelope.method,
                url,
                params=envelope.query or None,
                headers=headers or None,
                data=envelope.body,
            ) as resp:
                if resp.status in self._stale_statuses:
                    raise IdentityStaleError(
                        f"HTTP {envelope.method} {url} returned {resp.status}: stored credentials rejected"
                    )
                body = await resp.read()
                response_headers = {k: v for k, v in resp.headers.items()}
                return HttpResponse(status=resp.status, headers=response_headers, body=body)
        except TimeoutError as exc:
            raise TransportTimeoutError(f"HTTP {envelope.method} {url} timed out") from exc
        except aiohttp.ClientError as exc:
            raise TransportError(f"HTTP {envelope.method} {url} failed: {exc}") from exc

    def subscribe(self, envelope: HttpRequest) -> AsyncIterator[HttpResponse]:
        """Open a Server-Sent Events stream and yield one response per event.

        The request is issued with ``Accept: text/event-stream``; each SSE
        event's concatenated ``data:`` payload becomes an
        :class:`HttpResponse` body (status + headers copied from the streaming
        response) for the codec to decode. Closing the returned iterator closes
        the underlying connection.

        This is **push only** (native SSE). The opt-in polling fallback lives one
        layer up (repeated :meth:`request`), never here.

        Raises:
            TransportNotOpenError: if called before :meth:`open`.
            IdentityStaleError: on a 401/403 (stored credentials rejected), unless opted out.
            TransportTimeoutError: on aiohttp / asyncio timeout.
            TransportError: on a non-2xx status or connection failure.
        """

        async def _stream() -> AsyncIterator[HttpResponse]:
            if self._session is None or self._session.closed:
                raise TransportNotOpenError("HttpTransport.subscribe() called before open()")
            url = urljoin(self._base_url, envelope.path.lstrip("/"))
            headers = {"Accept": "text/event-stream", **self._default_headers, **envelope.headers}
            try:
                async with self._session.request(
                    envelope.method,
                    url,
                    params=envelope.query or None,
                    headers=headers,
                ) as resp:
                    if resp.status in self._stale_statuses:
                        raise IdentityStaleError(f"HTTP SSE {url} returned {resp.status}: stored credentials rejected")
                    if resp.status >= 400:
                        raise TransportError(f"HTTP SSE {url} returned {resp.status}")
                    response_headers = {k: v for k, v in resp.headers.items()}
                    async for payload in _iter_sse_events(resp.content):
                        yield HttpResponse(status=resp.status, headers=response_headers, body=payload)
            except TimeoutError as exc:
                raise TransportTimeoutError(f"HTTP SSE {url} timed out") from exc
            except aiohttp.ClientError as exc:
                raise TransportError(f"HTTP SSE {url} failed: {exc}") from exc

        return _stream()


async def _iter_sse_events(content: aiohttp.StreamReader) -> AsyncIterator[bytes]:
    """Yield each SSE event's concatenated ``data:`` payload as bytes.

    Follows the Server-Sent Events line grammar: ``data:`` lines accumulate
    (joined by newlines), a blank line dispatches the event, and ``:`` comments
    / other fields (``event:`` / ``id:`` / ``retry:``) are ignored.
    """
    data_lines: list[str] = []
    async for raw in content:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line == "":
            if data_lines:
                yield "\n".join(data_lines).encode("utf-8")
                data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:  # flush a trailing event with no final blank line
        yield "\n".join(data_lines).encode("utf-8")
