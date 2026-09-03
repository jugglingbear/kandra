"""HTTP :class:`Scanner` adapter that probes a list of candidate base URLs.

mDNS / SSD discovery is intentionally out of scope — that would
pull in :mod:`zeroconf` and platform-specific service-type knowledge.
This adapter handles the common "we already know a handful of likely
URLs (LAN broadcast IP, mDNS-resolved hostname, last known address);
probe each in parallel and tell me which ones are alive" pattern.

A *probe* is a single HTTP request whose 2xx response means "this URL
hosts the device I'm looking for". The default probe issues ``GET /`` —
override via the ``probe_path`` argument or by supplying your own
``probe_callable``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

import aiohttp

from kandra_runtime.errors import TransportError
from kandra_runtime.scanner import Candidate, Matcher, Scanner, accept_all

ProbeCallable = Callable[[aiohttp.ClientSession, str], Awaitable[Candidate | None]]
"""Async probe — receives the session + base URL, returns a :class:`Candidate` or ``None``."""


def _default_probe(probe_path: str) -> ProbeCallable:
    async def _probe(session: aiohttp.ClientSession, base_url: str) -> Candidate | None:
        url = base_url.rstrip("/") + probe_path
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return None
                advertised_name = resp.headers.get("Server")
                return Candidate(
                    transport="http",
                    address=base_url,
                    advertised_name=advertised_name,
                    metadata={"status": resp.status, "probe_url": url},
                )
        except (TimeoutError, aiohttp.ClientError):
            return None

    return _probe


class HttpScanner(Scanner):
    """HTTP adapter that probes a fixed set of base URLs in parallel."""

    def __init__(
        self,
        candidates: Iterable[str],
        *,
        probe_path: str = "/",
        per_probe_timeout: float = 2.0,
        probe_callable: ProbeCallable | None = None,
        session_factory: Callable[[], aiohttp.ClientSession] | None = None,
    ) -> None:
        """Create an HTTP scanner that probes ``candidates``.

        Parameters
        ----------
        candidates:
            Base URLs to probe (e.g. ``["http://192.168.1.42",
            "http://my-device.local"]``). Iterated once at construction.
        probe_path:
            Path appended to each base URL by the default probe.
            Ignored when ``probe_callable`` is supplied.
        per_probe_timeout:
            Per-request timeout passed to the aiohttp ``ClientSession``
            when ``session_factory`` is ``None``.
        probe_callable:
            Optional custom probe — receives the shared session and a
            base URL, returns a :class:`Candidate` or ``None``.
        session_factory:
            Optional factory for the aiohttp session — primarily for
            tests that want to inject a mock session.
        """
        self._candidates = list(candidates)
        if not self._candidates:
            raise ValueError("HttpScanner requires at least one candidate URL")
        self._probe = probe_callable or _default_probe(probe_path)
        self._per_probe_timeout = per_probe_timeout
        self._session_factory = session_factory or (
            lambda: aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=per_probe_timeout)
            )
        )

    def scan(
        self,
        *,
        matcher: Matcher = accept_all,
        timeout: float | None = None,
    ) -> AsyncIterator[Candidate]:
        """Probe each candidate URL in parallel; yield each match as it resolves."""

        async def _aiter() -> AsyncIterator[Candidate]:
            try:
                session = self._session_factory()
            except Exception as exc:
                raise TransportError(
                    f"HttpScanner: failed to construct aiohttp session: {exc}"
                ) from exc
            tasks = [asyncio.create_task(self._safe_probe(session, url)) for url in self._candidates]
            try:
                async for candidate in self._drain(tasks, timeout):
                    if matcher(candidate):
                        yield candidate
            finally:
                await _cancel_and_close(tasks, session)

        return _aiter()

    async def _safe_probe(self, session: aiohttp.ClientSession, url: str) -> Candidate | None:
        try:
            return await self._probe(session, url)
        except Exception:
            return None

    @staticmethod
    async def _drain(
        tasks: list[asyncio.Task[Candidate | None]],
        timeout: float | None,
    ) -> AsyncIterator[Candidate]:
        pending: set[asyncio.Task[Candidate | None]] = set(tasks)
        deadline = (
            asyncio.get_running_loop().time() + timeout if timeout is not None else None
        )
        while pending:
            remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
            if remaining is not None and remaining <= 0:
                return
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                return  # deadline reached without any completion
            for task in done:
                candidate = task.result()
                if candidate is not None:
                    yield candidate


async def _cancel_and_close(
    tasks: list[asyncio.Task[Candidate | None]],
    session: aiohttp.ClientSession,
) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    await session.close()
