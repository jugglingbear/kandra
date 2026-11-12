"""Tests for ``interpreter:`` -- a custom ResponseInterpreter on a transport, or on one operation, vs the default.

The family defaults (status codes for HTTP, always-accepted otherwise) are covered by the suites that build the
example devices. Here we cover the *custom* paths: a transport with ``interpreter:`` set must classify every reply with
that class, and an operation's own ``interpreter:`` must replace it for that operation only, in plain builds and in
vendored ``--profile`` builds.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest
from kandra.closure import ClosureError
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


class RejectEverything:
    def classify(self, response: HttpResponse) -> ClassificationVerdict:
        return ClassificationVerdict(Classification.REJECTED, reason="refused")


class NotAnInterpreter:
    pass
"""

_OPS = """\
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    percent: int


class PingLevel:
    value = Level


class PingBlip:
    payload = Level
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
  src/dev/ops.py: [internal, partner]
"""


def _write_sources(root: Path) -> None:
    files = {"dev/__init__.py": "", "dev/ping.py": _PING, "dev/interp.py": _INTERP, "dev/ops.py": _OPS}
    for rel, text in files.items():
        path = root / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "audience_profiles.yaml").write_text(_PROFILES, encoding="utf-8")


def _make_project(root: Path, *, interpreter: str = "dev.interp:EnvelopeInterpreter") -> Path:
    """Write a one-command HTTP device whose transport names ``interpreter``; return the manifest path."""
    _write_sources(root)
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


@contextmanager
def _on_path(import_roots: list[Path]) -> Iterator[None]:
    """Put ``import_roots`` on ``sys.path``; afterwards remove them and forget every module imported meanwhile."""
    snapshot = set(sys.modules)
    sys.path[:0] = [str(p) for p in import_roots]
    try:
        yield
    finally:
        for p in import_roots:
            with suppress(ValueError):
                sys.path.remove(str(p))
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]


async def _ping(import_roots: list[Path], ping_module: str, method: str, *values: int) -> list[Any]:
    """Import the generated SDK from ``import_roots`` and call ``client.ping.<method>`` once per value."""
    with _on_path(import_roots):
        sdk = importlib.import_module("mini_sdk")
        ping_request = importlib.import_module(ping_module).PingRequest
        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(_device)
        async with open_transport(transport) as t:
            client = sdk.MiniClient(transports={sdk.TransportId.HTTP: t})
            return [await getattr(client.ping, method)(ping_request(n=n)) for n in values]


async def _ping_both(import_roots: list[Path], ping_module: str) -> list[Any]:
    """Dispatch an accepted and a rejected ``ping.check``."""
    return await _ping(import_roots, ping_module, "check", 3, -1)


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


# ---------------------------------------------------------------------------
# Per-operation override: an operation's own `interpreter:` replaces the transport's for that operation only.
# ---------------------------------------------------------------------------


def _make_override_project(
    root: Path, *, override: str = "dev.interp:EnvelopeInterpreter", transport_interpreter: str | None = None
) -> Path:
    """Write an HTTP device where ``ping.legacy``, ``ping.level`` (read), and ``ping.blip`` name ``override``.

    ``ping.check`` names none, so it gets the transport's interpreter (or the HTTP default).
    """
    _write_sources(root)
    transport = "  - id: http\n    family: http\n"
    if transport_interpreter is not None:
        transport += f"    interpreter: {transport_interpreter}\n"
    common = "    transports: [http]\n    audience: [internal, partner]\n"
    manifest = root / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal, partner]\n"
        "source_roots: [src]\n"
        f"transports:\n{transport}"
        "commands:\n"
        f"  - id: ping.check\n    handler: dev.ping:Ping\n{common}"
        "    http:\n      http: {method: POST, path: /ping}\n"
        f"  - id: ping.legacy\n    handler: dev.ping:Ping\n{common}"
        f"    http:\n      http: {{method: POST, path: /legacy, interpreter: '{override}'}}\n"
        "attributes:\n"
        f"  - id: ping.level\n    handler: dev.ops:PingLevel\n    operations: [read]\n{common}"
        f"    http:\n      http:\n        read: {{path: /level, interpreter: '{override}'}}\n"
        "events:\n"
        f"  - id: ping.blip\n    handler: dev.ops:PingBlip\n{common}"
        f"    http:\n      http: {{path: /blips, interpreter: '{override}'}}\n",
        encoding="utf-8",
    )
    return manifest


async def test_operation_interpreter_applies_to_that_operation_only(tmp_path: Path) -> None:
    """Same device, same 200-with-an-error reply: only the command that names an interpreter reports REJECTED."""
    build_sdk(_make_override_project(tmp_path), output_root=tmp_path / "out")
    roots = [tmp_path / "src", tmp_path / "out"]

    (plain,) = await _ping(roots, "dev.ping", "check", -1)
    ok, refused = await _ping(roots, "dev.ping", "legacy", 3, -1)

    assert plain.classification.name == "ANOMALOUS"  # the HTTP default accepts the 200, then the body fails to decode
    assert ok.accepted and ok.data.n == 3
    assert refused.classification.name == "REJECTED"
    assert refused.reason == "negative"


def test_operation_interpreter_beats_the_transport_interpreter(tmp_path: Path) -> None:
    """Precedence is operation, then transport -- for commands, attribute operations, and events alike."""
    manifest = _make_override_project(
        tmp_path, override="dev.interp:RejectEverything", transport_interpreter="dev.interp:EnvelopeInterpreter"
    )
    build_sdk(manifest, output_root=tmp_path / "out", typecheck=True)

    with _on_path([tmp_path / "src", tmp_path / "out"]):
        registry = importlib.import_module("mini_sdk.registry")
        chosen = {
            op: type(entry.interpreter).__name__
            for op, by_tid in registry.COMMANDS.items()
            for entry in by_tid.values()
        }

    assert chosen == {
        "ping.check": "EnvelopeInterpreter",
        "ping.legacy": "RejectEverything",
        "ping.level.read": "RejectEverything",
        "ping.blip.subscribe": "RejectEverything",
    }


async def test_vendored_profile_uses_operation_interpreter(tmp_path: Path) -> None:
    """A ``--profile`` build vendors an operation's interpreter and imports it from ``_internal``."""
    result = build_sdk(_make_override_project(tmp_path), output_root=tmp_path / "out", profile="partner", clean=True)
    registry = (result.package_path / "registry.py").read_text(encoding="utf-8")
    assert "from mini_sdk._internal.dev.interp import EnvelopeInterpreter as _OpInterp_ping_legacy_http" in registry

    (refused,) = await _ping([tmp_path / "out" / "partner"], "mini_sdk._internal.dev.ping", "legacy", -1)

    assert refused.classification.name == "REJECTED"


def test_operation_interpreter_without_classify_fails_the_build(tmp_path: Path) -> None:
    """The error names the operation and transport, so the bad override is easy to find."""
    manifest = _make_override_project(tmp_path, override="dev.interp:NotAnInterpreter")
    with pytest.raises(BuildError, match=r"command 'ping\.legacy' interpreter .* on transport 'http' has no classify"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_operation_interpreter_must_live_under_a_source_root(tmp_path: Path) -> None:
    """Like every class the manifest names, an operation's interpreter is vendored, so it must be under a root."""
    manifest = _make_override_project(tmp_path, override="collections:OrderedDict")
    with pytest.raises(ClosureError, match="not under any source root"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_operation_interpreter_must_be_a_dotted_path() -> None:
    text = (
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal]\n"
        "source_roots: [src]\n"
        "transports:\n  - id: http\n    family: http\n"
        "commands:\n  - id: ping.check\n    handler: dev.ping:Ping\n    transports: [http]\n    audience: [internal]\n"
        "    http:\n      http: {method: GET, path: /, interpreter: not-a-path}\n"
    )
    with pytest.raises(LoaderError, match="interpreter"):
        load_manifest(text)
