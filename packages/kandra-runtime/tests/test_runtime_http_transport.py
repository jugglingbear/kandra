"""HttpTransport tests: exercise GET/POST/PUT/DELETE + drop-connection against a live aiohttp server."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from kandra_runtime import (
    Classification,
    Command,
    HttpJsonCodec,
    HttpRequest,
    HttpTransport,
    TransportNotOpenError,
    default_http_interpreter,
    dispatch,
)
from kandra_runtime.errors import TransportError

# ---------------------------------------------------------------------------
# Fixtures: a tiny aiohttp echo / sink server.
# ---------------------------------------------------------------------------


async def _hello(request: web.Request) -> web.Response:
    name = request.query.get("name", "world")
    return web.json_response({"greeting": f"hello {name}"})


async def _echo(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response({"you_sent": body})


async def _replace(request: web.Request) -> web.Response:
    body = await request.json()
    return web.json_response({"replaced_with": body, "method": request.method})


async def _delete(request: web.Request) -> web.Response:
    return web.json_response({"deleted": request.match_info["item"]})


async def _hang(_request: web.Request) -> web.Response:
    """Sleep forever; client should hit its timeout."""
    await asyncio.sleep(10)
    return web.json_response({"never": "reached"})  # pragma: no cover


@pytest.fixture
async def server() -> AsyncIterator[TestServer]:
    app = web.Application()
    app.router.add_get("/hello", _hello)
    app.router.add_post("/echo", _echo)
    app.router.add_put("/replace", _replace)
    app.router.add_delete("/items/{item}", _delete)
    app.router.add_post("/hang", _hang)
    test_server = TestServer(app)
    await test_server.start_server()
    try:
        yield test_server
    finally:
        await test_server.close()


@pytest.fixture
async def transport(server: TestServer) -> AsyncIterator[HttpTransport]:
    t = HttpTransport(str(server.make_url("/")))
    await t.open()
    try:
        yield t
    finally:
        await t.close()


# ---------------------------------------------------------------------------
# Method coverage.
# ---------------------------------------------------------------------------


async def test_get_with_query(transport: HttpTransport) -> None:
    resp = await transport.request(HttpRequest(method="GET", path="/hello", query={"name": "bear"}))
    assert resp.status == 200
    assert json.loads(resp.body) == {"greeting": "hello bear"}


async def test_post_with_body(transport: HttpTransport) -> None:
    body = json.dumps({"pressure_psi": 12}).encode()
    resp = await transport.request(
        HttpRequest(method="POST", path="/echo", headers={"Content-Type": "application/json"}, body=body)
    )
    assert resp.status == 200
    assert json.loads(resp.body) == {"you_sent": {"pressure_psi": 12}}


async def test_put_with_body(transport: HttpTransport) -> None:
    body = json.dumps({"replacement": "bear"}).encode()
    resp = await transport.request(
        HttpRequest(method="PUT", path="/replace", headers={"Content-Type": "application/json"}, body=body)
    )
    assert resp.status == 200
    assert json.loads(resp.body) == {"replaced_with": {"replacement": "bear"}, "method": "PUT"}


async def test_delete(transport: HttpTransport) -> None:
    resp = await transport.request(HttpRequest(method="DELETE", path="/items/poke"))
    assert resp.status == 200
    assert json.loads(resp.body) == {"deleted": "poke"}


# ---------------------------------------------------------------------------
# Lifecycle & error paths.
# ---------------------------------------------------------------------------


async def test_request_before_open_raises(server: TestServer) -> None:
    t = HttpTransport(str(server.make_url("/")))
    with pytest.raises(TransportNotOpenError):
        await t.request(HttpRequest(method="GET", path="/hello"))


async def test_async_context_manager(server: TestServer) -> None:
    async with HttpTransport(str(server.make_url("/"))) as t:
        assert t.is_open
        resp = await t.request(HttpRequest(method="GET", path="/hello"))
        assert resp.status == 200
    assert not t.is_open


async def test_connection_error_wrapped(server: TestServer) -> None:
    # Bind to a closed port — using the server's host but a known-unused port.
    await server.close()
    t = HttpTransport(f"http://{server.host}:{server.port}/")
    await t.open()
    try:
        with pytest.raises(TransportError):
            await t.request(HttpRequest(method="GET", path="/hello"))
    finally:
        await t.close()


# ---------------------------------------------------------------------------
# expects_response=False end-to-end.
# ---------------------------------------------------------------------------


async def test_fire_and_forget_swallows_timeout(transport: HttpTransport) -> None:
    """A command with expects_response=False + a short timeout against a hanging endpoint must return None."""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Req:
        pass

    @dataclass(frozen=True)
    class _Resp:
        pass

    codec: HttpJsonCodec[_Req, _Resp] = HttpJsonCodec(
        method="POST", path="/hang", request_type=_Req, response_type=_Resp
    )
    command: Command[_Req, _Resp, HttpRequest, object] = Command(
        id="test.hang",
        codec=codec,
        interpreter=default_http_interpreter,
        timeout=0.2,
        expects_response=False,
    )
    result = await dispatch(command, transport, _Req())
    assert result is None


async def test_timeout_yields_transport_failure_result(transport: HttpTransport) -> None:
    """Same hanging endpoint but expects_response=True must surface as TRANSPORT_FAILURE."""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Req:
        pass

    @dataclass(frozen=True)
    class _Resp:
        pass

    codec: HttpJsonCodec[_Req, _Resp] = HttpJsonCodec(
        method="POST", path="/hang", request_type=_Req, response_type=_Resp
    )
    command: Command[_Req, _Resp, HttpRequest, object] = Command(
        id="test.hang",
        codec=codec,
        interpreter=default_http_interpreter,
        timeout=0.2,
        expects_response=True,
    )
    result = await dispatch(command, transport, _Req())
    assert result is not None
    assert result.classification is Classification.TRANSPORT_FAILURE
    assert "timed out" in result.reason
