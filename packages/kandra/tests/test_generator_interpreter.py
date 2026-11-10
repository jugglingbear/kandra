"""Tests for the transport ``interpreter:`` field -- a custom ResponseInterpreter vs the family default.

The family defaults (status codes for HTTP, always-accepted otherwise) are covered by the suites that build the
example devices. Here we cover the *custom* path: a transport with ``interpreter:`` set must classify every reply with
that class, in plain builds and in vendored ``--profile`` builds.
"""

from __future__ import annotations

import importlib
import json
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from kandra.generator import BuildError, build_sdk
from kandra.loader import LoaderError, load_manifest
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

# A device that always answers 200 and reports failure in the body instead.
_INTERP = """\
from __future__ import annotations

import json

from kandra_runtime import Classification, ClassificationVerdict, HttpResponse


class EnvelopeInterpreter:
    def classify(self, response: HttpResponse) -> ClassificationVerdict:
        error = json.loads(response.body or b"{}").get("error")
        if error is None:
            return ClassificationVerdict(Classification.ACCEPTED)
        return ClassificationVerdict(Classification.REJECTED, reason=str(error))


class NotAnInterpreter:
    pass
"""

_PROFILES = """\
profiles:
  partner:
    include_audience: [partner]
    deny_substrings: []
files:
  src/dev/__init__.py: [internal, partner]
  src/dev/ping.py: [internal, partner]
  src/dev/interp.py: [internal, partner]
"""


def _make_project(root: Path, *, interpreter: str = "dev.interp:EnvelopeInterpreter") -> Path:
    """Write a one-command HTTP device whose transport names ``interpreter``; return the manifest path."""
    for rel, text in {"dev/__init__.py": "", "dev/ping.py": _PING, "dev/interp.py": _INTERP}.items():
        path = root / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "audience_profiles.yaml").write_text(_PROFILES, encoding="utf-8")
    manifest = root / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal, partner]\n"
        "source_roots: [src]\n"
        f"transports:\n  - id: http\n    family: http\n    interpreter: {interpreter}\n"
        "commands:\n"
        "  - id: ping.check\n"
        "    handler: dev.ping:Ping\n"
        "    transports: [http]\n"
        "    audience: [internal, partner]\n"
        "    http:\n      http:\n        method: POST\n        path: /ping\n",
        encoding="utf-8",
    )
    return manifest


def _device(req: HttpRequest) -> HttpResponse:
    """Echo ``n`` back with a 200, except reject ``n < 0`` -- still with a 200, error in the body."""
    n = json.loads((req.body or b"{}").decode("utf-8"))["n"]
    body = {"error": "negative"} if n < 0 else {"n": n}
    return HttpResponse(status=200, body=json.dumps(body).encode("utf-8"))


async def _ping_both(import_roots: list[Path], ping_module: str) -> tuple[Any, Any]:
    """Import the generated SDK from ``import_roots`` and dispatch an accepted and a rejected ping."""
    snapshot = set(sys.modules)
    sys.path[:0] = [str(p) for p in import_roots]
    try:
        sdk = importlib.import_module("mini_sdk")
        ping_request = importlib.import_module(ping_module).PingRequest
        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_device)
        async with open_transport(transport) as t:
            client = sdk.MiniClient(transports={sdk.TransportId.HTTP: t})
            return await client.ping.check(ping_request(n=3)), await client.ping.check(ping_request(n=-1))
    finally:
        for p in import_roots:
            with suppress(ValueError):
                sys.path.remove(str(p))
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]


def test_registry_wires_the_transport_interpreter(tmp_path: Path) -> None:
    """The registry imports and instantiates the custom interpreter (mypy-strict clean), not the HTTP default."""
    result = build_sdk(_make_project(tmp_path), output_root=tmp_path / "out", typecheck=True)
    registry = (result.package_path / "registry.py").read_text(encoding="utf-8")

    assert "from dev.interp import EnvelopeInterpreter as _Interp_http" in registry
    assert "interpreter=_Interp_http()," in registry
    assert "default_http_interpreter" not in registry


async def test_custom_interpreter_classifies_replies(tmp_path: Path) -> None:
    """A 200 reply carrying an error is REJECTED by the custom interpreter; a clean 200 is ACCEPTED."""
    build_sdk(_make_project(tmp_path), output_root=tmp_path / "out")

    ok, refused = await _ping_both([tmp_path / "src", tmp_path / "out"], "dev.ping")

    assert ok.accepted and ok.data.n == 3
    assert refused.classification.name == "REJECTED"
    assert refused.reason == "negative"


async def test_vendored_profile_uses_interpreter_without_source_roots(tmp_path: Path) -> None:
    """A ``--profile`` build vendors the interpreter and imports it from ``_internal``, with the source roots OFF."""
    result = build_sdk(_make_project(tmp_path), output_root=tmp_path / "out", profile="partner", clean=True)
    registry = (result.package_path / "registry.py").read_text(encoding="utf-8")
    assert "from mini_sdk._internal.dev.interp import EnvelopeInterpreter as _Interp_http" in registry

    ok, refused = await _ping_both([tmp_path / "out" / "partner"], "mini_sdk._internal.dev.ping")

    assert ok.accepted
    assert refused.classification.name == "REJECTED"


def test_interpreter_without_classify_fails_the_build(tmp_path: Path) -> None:
    """Naming a class with no ``classify`` method is a loud build error, not a broken SDK."""
    manifest = _make_project(tmp_path, interpreter="dev.interp:NotAnInterpreter")
    with pytest.raises(BuildError, match="has no classify"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_interpreter_must_be_a_dotted_path() -> None:
    """The manifest rejects an ``interpreter`` that is not ``module:Class``."""
    text = (
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal]\n"
        "source_roots: [src]\n"
        "transports:\n  - id: http\n    family: http\n    interpreter: not-a-path\n"
        "commands: []\n"
    )
    with pytest.raises(LoaderError, match="interpreter"):
        load_manifest(text)
