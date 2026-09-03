"""Focused error-path coverage for codecs, transport validation, and store rollback.

These tests target the small residual gaps in the runtime coverage report
that aren't covered by the broader behavioral suites.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from kandra_runtime import (
    BleChannelCodec,
    BleIdentity,
    CodecError,
    HttpJsonCodec,
    HttpResponse,
    HttpTransport,
    PlatformDirsJsonStore,
)
from kandra_runtime.http import _stringify  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# BleChannelCodec error paths
# ---------------------------------------------------------------------------


@dataclass
class _BleReq:
    n: int


def test_ble_channel_codec_rejects_empty_channel() -> None:
    with pytest.raises(ValueError, match="channel must be non-empty"):
        BleChannelCodec[Any, Any](channel="", payload_codec=None)


def test_ble_channel_codec_encode_without_payload_codec_raises() -> None:
    codec: BleChannelCodec[_BleReq, Any] = BleChannelCodec(channel="cmd", payload_codec=None)
    with pytest.raises(CodecError, match="no payload codec"):
        codec.encode(_BleReq(n=1))


def test_ble_channel_codec_decode_without_payload_codec_raises() -> None:
    codec: BleChannelCodec[Any, Any] = BleChannelCodec(channel="cmd", payload_codec=None)
    with pytest.raises(CodecError, match="no payload codec"):
        codec.decode(b"\x00\x01")


def test_ble_channel_codec_encode_rejects_non_bytes_payload() -> None:
    class _BadCodec:
        def encode(self, request: object) -> Any:
            return "not-bytes"  # wrong type on purpose

        def decode(self, response: Any) -> object:
            return response

    codec: BleChannelCodec[_BleReq, Any] = BleChannelCodec(
        channel="cmd", payload_codec=_BadCodec()  # type: ignore[arg-type]
    )
    with pytest.raises(CodecError, match="expected bytes"):
        codec.encode(_BleReq(n=1))


# ---------------------------------------------------------------------------
# HttpJsonCodec error paths
# ---------------------------------------------------------------------------


@dataclass
class _HttpReq:
    name: str
    count: int


@dataclass
class _HttpResp:
    ok: bool


def test_http_json_codec_encode_rejects_non_dataclass() -> None:
    codec: HttpJsonCodec[Any, _HttpResp] = HttpJsonCodec(
        method="POST", path="/x", request_type=_HttpReq, response_type=_HttpResp
    )
    with pytest.raises(CodecError, match="dataclass instance"):
        codec.encode({"name": "x"})  # type: ignore[arg-type]


def test_http_json_codec_encode_rejects_dataclass_class_not_instance() -> None:
    codec: HttpJsonCodec[Any, _HttpResp] = HttpJsonCodec(
        method="POST", path="/x", request_type=_HttpReq, response_type=_HttpResp
    )
    with pytest.raises(CodecError, match="dataclass instance"):
        codec.encode(_HttpReq)  # type: ignore[arg-type]


def test_http_json_codec_query_from_request_emits_query_params_only() -> None:
    codec: HttpJsonCodec[_HttpReq, _HttpResp] = HttpJsonCodec(
        method="GET",
        path="/list",
        request_type=_HttpReq,
        response_type=_HttpResp,
        query_from_request=True,
    )
    envelope = codec.encode(_HttpReq(name="alpha", count=3))
    assert envelope.body is None or envelope.body == b""
    assert envelope.query == {"name": "alpha", "count": "3"}
    assert "Content-Type" not in envelope.headers


def test_http_json_codec_query_drops_none_fields() -> None:
    @dataclass
    class _OptReq:
        keep: str
        drop: str | None

    codec: HttpJsonCodec[_OptReq, _HttpResp] = HttpJsonCodec(
        method="GET",
        path="/q",
        request_type=_OptReq,
        response_type=_HttpResp,
        query_from_request=True,
    )
    env = codec.encode(_OptReq(keep="yes", drop=None))
    assert env.query == {"keep": "yes"}


def test_http_json_codec_decode_without_response_type_raises() -> None:
    codec: HttpJsonCodec[_HttpReq, None] = HttpJsonCodec(
        method="POST", path="/fire", request_type=_HttpReq, response_type=None
    )
    with pytest.raises(CodecError, match="declared no response type"):
        codec.decode(HttpResponse(status=204, headers={}, body=b""))


def test_http_json_codec_decode_empty_body_raises() -> None:
    codec: HttpJsonCodec[_HttpReq, _HttpResp] = HttpJsonCodec(
        method="POST", path="/x", request_type=_HttpReq, response_type=_HttpResp
    )
    with pytest.raises(CodecError, match="empty response body"):
        codec.decode(HttpResponse(status=200, headers={}, body=b""))


def test_http_json_codec_decode_missing_required_field_raises() -> None:
    codec: HttpJsonCodec[_HttpReq, _HttpResp] = HttpJsonCodec(
        method="POST", path="/x", request_type=_HttpReq, response_type=_HttpResp
    )
    # Body is valid JSON but missing the `ok` field that `_HttpResp` requires.
    with pytest.raises(CodecError, match="cannot build _HttpResp"):
        codec.decode(HttpResponse(status=200, headers={}, body=b"{}"))


@pytest.mark.parametrize(
    "value, expected",
    [
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (3.14, "3.14"),
        ("hello", "hello"),
        (None, "None"),
    ],
)
def test_stringify_renders_scalar_values(value: object, expected: str) -> None:
    assert _stringify(value) == expected


# ---------------------------------------------------------------------------
# HttpTransport input validation
# ---------------------------------------------------------------------------


def test_http_transport_rejects_empty_base_url() -> None:
    with pytest.raises(ValueError, match="base_url must be non-empty"):
        HttpTransport("")


def test_http_transport_normalizes_trailing_slash() -> None:
    # Constructor adds a trailing slash so urljoin treats base_url as a directory.
    t1 = HttpTransport("http://example.com/api")
    t2 = HttpTransport("http://example.com/api/")
    assert t1._base_url == "http://example.com/api/"  # type: ignore[attr-defined]
    assert t2._base_url == "http://example.com/api/"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_http_transport_close_idempotent_without_open() -> None:
    """Calling close() before open() is a no-op, not an error."""
    t = HttpTransport("http://example.com")
    await t.close()  # must not raise


@pytest.mark.asyncio
async def test_http_transport_close_with_injected_session_is_noop() -> None:
    """Injected sessions are owned by the caller — close() must not touch them."""
    import aiohttp

    session = aiohttp.ClientSession()
    try:
        t = HttpTransport("http://example.com", session=session)
        await t.open()  # injected session path — early-returns
        await t.close()  # owner-controlled, no-op
        assert not session.closed
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# PlatformDirsJsonStore atomic-write rollback
# ---------------------------------------------------------------------------


def test_platformdirs_store_rolls_back_tempfile_on_write_failure(tmp_path: Path) -> None:
    """If os.replace fails mid-write, the temp file is cleaned up and the error propagates."""
    store = PlatformDirsJsonStore(app_name="kandra_test", directory=tmp_path)
    boom = OSError("simulated replace failure")

    with (
        patch("kandra_runtime.identity_store_file.os.replace", side_effect=boom),
        pytest.raises(OSError, match="simulated replace failure"),
    ):
        store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))

    # No leftover .tmp files in the directory.
    leftover = [p.name for p in tmp_path.iterdir() if p.name.endswith(".json.tmp")]
    assert leftover == [], f"temp file not cleaned up: {leftover}"
    # The store file itself was never created.
    assert not store.path.exists()


def test_platformdirs_store_rollback_swallows_unlink_failure(tmp_path: Path) -> None:
    """If the temp file vanishes before cleanup, the original error still propagates."""
    store = PlatformDirsJsonStore(app_name="kandra_test", directory=tmp_path)

    with (
        patch("kandra_runtime.identity_store_file.os.replace", side_effect=OSError("replace fail")),
        patch("kandra_runtime.identity_store_file.os.unlink", side_effect=OSError("unlink fail")),
        # Original ``replace`` failure must still be the one raised.
        pytest.raises(OSError, match="replace fail"),
    ):
        store.save(BleIdentity(saved_name="poker", address="AA:BB:CC:DD:EE:FF"))


# ---------------------------------------------------------------------------
# loopback.is_open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loopback_transport_is_open_property() -> None:
    from kandra_runtime import LoopbackTransport

    async def echo(request: object) -> object:
        return request

    t: LoopbackTransport[object, object] = LoopbackTransport(echo)
    assert t.is_open is False
    await t.open()
    assert t.is_open is True
    await t.close()
    assert t.is_open is False
