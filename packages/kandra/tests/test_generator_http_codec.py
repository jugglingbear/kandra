"""Tests for ``codec:`` on an HTTP transport -- the default codec for every command on that transport.

Without it, HTTP commands use the runtime ``HttpJsonCodec``; a per-command ``codec:`` still overrides either default.
"""

from __future__ import annotations

import importlib
import json
import sys
from contextlib import suppress
from pathlib import Path

from kandra.generator import build_sdk
from kandra_runtime import HttpRequest, HttpResponse, LoopbackTransport, open_transport

_PING = """\
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PingRequest:
    n: int


@dataclass(frozen=True)
class PingResponse:
    n: int


class Ping:
    request = PingRequest
    response = PingResponse
"""

# A device whose API speaks form-encoded bodies, with one JSON endpoint.
_CODECS = """\
from __future__ import annotations

import dataclasses
from typing import Any, TypeVar
from urllib.parse import parse_qs, urlencode

from kandra_runtime import HttpJsonCodec, HttpRequest, HttpResponse

ReqT = TypeVar("ReqT")
RespT = TypeVar("RespT")


class FormCodec(HttpJsonCodec[ReqT, RespT]):
    def encode(self, request: ReqT) -> HttpRequest:
        body = urlencode(dataclasses.asdict(request)).encode("utf-8")  # type: ignore[call-overload]
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        return HttpRequest(method=self._method, path=self._path, headers=headers, body=body)

    def decode(self, response: HttpResponse) -> RespT:
        assert self._response_type is not None
        fields: dict[str, Any] = {k: int(v[0]) for k, v in parse_qs(response.body.decode("utf-8")).items()}
        return self._response_type(**fields)


class JsonAgain(HttpJsonCodec[ReqT, RespT]):
    pass
"""

_PROFILES = """\
profiles:
  internal:
    include_audience: [internal]
    deny_substrings: []
files: {}
"""


def _make_project(root: Path) -> Path:
    """Write a two-command HTTP device whose transport codec is ``FormCodec``; return the manifest path."""
    for rel, text in {"dev/__init__.py": "", "dev/ping.py": _PING, "dev/codecs.py": _CODECS}.items():
        path = root / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "audience_profiles.yaml").write_text(_PROFILES, encoding="utf-8")
    manifest = root / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal]\n"
        "source_roots: [src]\n"
        "transports:\n  - id: http\n    family: http\n    codec: dev.codecs:FormCodec\n"
        "commands:\n"
        "  - id: ping.form\n    handler: dev.ping:Ping\n    transports: [http]\n    audience: [internal]\n"
        "    http:\n      http:\n        method: POST\n        path: /form\n"
        "  - id: ping.json\n    handler: dev.ping:Ping\n    transports: [http]\n    audience: [internal]\n"
        "    http:\n      http:\n        method: POST\n        path: /json\n        codec: dev.codecs:JsonAgain\n",
        encoding="utf-8",
    )
    return manifest


def test_transport_codec_is_the_default_and_per_command_codec_wins(tmp_path: Path) -> None:
    """``ping.form`` uses the transport's codec, ``ping.json`` its own override; neither needs ``HttpJsonCodec``."""
    result = build_sdk(_make_project(tmp_path), output_root=tmp_path / "out", typecheck=True)
    registry = (result.package_path / "registry.py").read_text(encoding="utf-8")
    form_entry, json_entry = registry.split('    "ping.json": {', 1)

    assert "from dev.codecs import FormCodec as _Codec_http" in registry
    assert "codec=_Codec_http(" in form_entry
    assert "codec=_CmdCodec_ping_json_http(" in json_entry
    assert "HttpJsonCodec" not in registry


async def test_transport_codec_shapes_the_wire(tmp_path: Path) -> None:
    """The transport codec form-encodes the request and parses the reply; the override still sends JSON."""
    build_sdk(_make_project(tmp_path), output_root=tmp_path / "out")
    seen: list[HttpRequest] = []

    def device(req: HttpRequest) -> HttpResponse:
        seen.append(req)
        if req.path == "/form":
            return HttpResponse(status=200, body=req.body or b"")
        return HttpResponse(status=200, body=req.body or b"{}")

    snapshot = set(sys.modules)
    roots = [str(tmp_path / "src"), str(tmp_path / "out")]
    sys.path[:0] = roots
    try:
        sdk = importlib.import_module("mini_sdk")
        ping_request = importlib.import_module("dev.ping").PingRequest
        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(device)
        async with open_transport(transport) as t:
            client = sdk.MiniClient(transports={sdk.TransportId.HTTP: t})
            form = await client.ping.form(ping_request(n=3))
            as_json = await client.ping.json(ping_request(n=4))
    finally:
        for p in roots:
            with suppress(ValueError):
                sys.path.remove(p)
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]

    assert seen[0].body == b"n=3"
    assert (seen[0].headers or {})["Content-Type"] == "application/x-www-form-urlencoded"
    assert form.accepted and form.data.n == 3
    assert json.loads(seen[1].body or b"") == {"n": 4}
    assert as_json.accepted and as_json.data.n == 4


def test_profile_build_vendors_the_transport_codec(tmp_path: Path) -> None:
    """A ``--profile`` build imports the transport codec from the vendored ``_internal`` tree."""
    result = build_sdk(_make_project(tmp_path), output_root=tmp_path / "out", profile="internal", clean=True)
    registry = (result.package_path / "registry.py").read_text(encoding="utf-8")

    assert "from mini_sdk._internal.dev.codecs import FormCodec as _Codec_http" in registry
