"""Tests for :mod:`kandra.generator.build` error and edge branches."""

from __future__ import annotations

import types
from importlib import metadata
from pathlib import Path

import pytest
from kandra.generator import BuildError, build_sdk
from kandra.generator import build as build_mod

_GOOD_HANDLER = (
    "from dataclasses import dataclass\n\n\n"
    "@dataclass(frozen=True)\nclass Req:\n    n: int\n\n\n"
    "@dataclass(frozen=True)\nclass Resp:\n    n: int\n\n\n"
    "class H:\n    request = Req\n    response = Resp\n"
)


def _project(
    root: Path,
    *,
    handler_ref: str = "dev.h:H",
    handler_src: str | None = _GOOD_HANDLER,
    command_id: str = "ping.check",
    source_roots: str = "[src]",
    extra_manifest: str = "",
) -> Path:
    """Write a minimal manifest + optional handler module; return the manifest path."""
    src = root / "src"
    (src / "dev").mkdir(parents=True, exist_ok=True)
    (src / "dev" / "__init__.py").write_text("", encoding="utf-8")
    if handler_src is not None:
        (src / "dev" / "h.py").write_text(handler_src, encoding="utf-8")
    manifest = root / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal, partner]\n"
        f"source_roots: {source_roots}\n"
        "transports:\n  - id: http\n    codec: k.c:C\n    family: http\n"
        "commands:\n"
        f"  - id: {command_id}\n    handler: {handler_ref}\n    transports: [http]\n"
        "    audience: [internal, partner]\n"
        "    http:\n      http:\n        method: GET\n        path: /\n"
        f"{extra_manifest}",
        encoding="utf-8",
    )
    return manifest


def test_handler_missing_request_attr_raises(tmp_path: Path) -> None:
    src = (
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\nclass Resp:\n    n: int\n\n\n"
        "class H:\n    response = Resp\n"
    )
    manifest = _project(tmp_path, handler_src=src)
    with pytest.raises(BuildError, match="missing required attribute 'request'"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_handler_request_not_a_class_raises(tmp_path: Path) -> None:
    src = (
        "from dataclasses import dataclass\n\n\n@dataclass\nclass Resp:\n    n: int\n\n\n"
        "class H:\n    request = 5\n    response = Resp\n"
    )
    manifest = _project(tmp_path, handler_src=src)
    with pytest.raises(BuildError, match="handler.request must be a class"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_handler_module_import_failure_raises(tmp_path: Path) -> None:
    manifest = _project(tmp_path, handler_ref="dev.missing:H", handler_src=None)
    with pytest.raises(BuildError, match="cannot import module 'dev.missing'"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_handler_attr_missing_raises(tmp_path: Path) -> None:
    manifest = _project(tmp_path, handler_ref="dev.h:Nope", handler_src="X = 1\n")
    with pytest.raises(BuildError, match="has no attribute 'Nope'"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_handler_attr_not_a_class_raises(tmp_path: Path) -> None:
    manifest = _project(tmp_path, handler_ref="dev.h:H", handler_src="H = 5\n")
    with pytest.raises(BuildError, match="expected a class"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_command_id_without_dot_raises(tmp_path: Path) -> None:
    manifest = _project(tmp_path, command_id="ping")
    with pytest.raises(BuildError, match="at least one dot"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_missing_source_roots_raises(tmp_path: Path) -> None:
    manifest = _project(tmp_path, source_roots="[does_not_exist]")
    with pytest.raises(BuildError, match="source_roots do not exist"):
        build_sdk(manifest, output_root=tmp_path / "out")


def test_non_profile_clean_rebuild_removes_dir(tmp_path: Path) -> None:
    manifest = _project(tmp_path)
    out = tmp_path / "out"
    first = build_sdk(manifest, output_root=out)
    stale = first.package_path / "_stale.py"
    stale.write_text("# stale\n", encoding="utf-8")
    build_sdk(manifest, output_root=out, clean=True)
    assert not stale.exists()


def test_kandra_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(build_mod.metadata, "version", boom)
    assert build_mod._kandra_version() == "0+unknown"


def test_profile_build_without_discovery_succeeds(tmp_path: Path) -> None:
    # A mini project has no discovery: block -> exercises the discovery=None path
    # through the full vendoring pipeline.
    manifest = _project(tmp_path)
    (tmp_path / "audience_profiles.yaml").write_text(
        "profiles:\n" "  partner:\n    include_audience: [internal, partner]\n",
        encoding="utf-8",
    )
    result = build_sdk(manifest, output_root=tmp_path / "out", profile="partner", clean=True)
    assert (result.package_path / "_internal" / "dev" / "h.py").is_file()
    assert not (result.package_path / "scanners.py").exists()


def test_profile_drops_ungranted_asset(tmp_path: Path) -> None:
    # An extra_include asset that the profile doesn't grant is pruned from the tree.
    manifest = _project(
        tmp_path,
        extra_manifest="vendoring:\n  extra_include: [dev/data.txt]\n",
    )
    (tmp_path / "src" / "dev" / "data.txt").write_text("payload\n", encoding="utf-8")
    (tmp_path / "audience_profiles.yaml").write_text(
        "profiles:\n"
        "  partner:\n    include_audience: [partner]\n"
        "files:\n"
        "  src/dev/__init__.py: [internal, partner]\n"
        "  src/dev/h.py: [internal, partner]\n"
        "  src/dev/data.txt: [internal]\n",  # asset internal-only -> dropped for partner
        encoding="utf-8",
    )
    result = build_sdk(manifest, output_root=tmp_path / "out", profile="partner", clean=True)
    assert not (result.package_path / "_internal" / "dev" / "data.txt").exists()
    assert (result.package_path / "_internal" / "dev" / "h.py").is_file()


def test_profile_clean_rebuild_removes_stale(tmp_path: Path) -> None:
    manifest = _project(tmp_path)
    (tmp_path / "audience_profiles.yaml").write_text(
        "profiles:\n  partner:\n    include_audience: [internal, partner]\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    first = build_sdk(manifest, output_root=out, profile="partner")
    stale = first.package_path / "_stale.py"
    stale.write_text("# stale\n", encoding="utf-8")
    build_sdk(manifest, output_root=out, profile="partner", clean=True)
    assert not stale.exists()


def test_profile_external_request_module_is_not_vendored(tmp_path: Path) -> None:
    # A handler whose request/response types come from stdlib (external) exercises
    # the "entry module resolves outside source_roots -> skip" path.
    src = "from decimal import Decimal\n\n\nclass H:\n    request = Decimal\n    response = Decimal\n"
    manifest = _project(tmp_path, handler_src=src)
    (tmp_path / "audience_profiles.yaml").write_text(
        "profiles:\n  partner:\n    include_audience: [internal, partner]\n",
        encoding="utf-8",
    )
    result = build_sdk(manifest, output_root=tmp_path / "out", profile="partner", clean=True)
    assert not (result.package_path / "_internal" / "decimal.py").exists()
    assert (result.package_path / "_internal" / "dev" / "h.py").is_file()


def test_loopback_transport_build_emits_user_codec_and_no_connect(tmp_path: Path) -> None:
    # A loopback-only device exercises the loopback command-entry rendering and
    # the "no persistent-identity transports -> no connect() codegen" branch.
    src = tmp_path / "src"
    (src / "dev" / "handlers").mkdir(parents=True)
    (src / "dev" / "__init__.py").write_text("", encoding="utf-8")
    (src / "dev" / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (src / "dev" / "codec.py").write_text(
        "class LoopCodec:\n"
        "    def __init__(self, request_type, response_type):\n"
        "        self._request_type = request_type\n"
        "        self._response_type = response_type\n\n"
        "    def encode(self, request):\n"
        "        return b''\n\n"
        "    def decode(self, payload):\n"
        "        return self._response_type()\n",
        encoding="utf-8",
    )
    (src / "dev" / "handlers" / "ping.py").write_text(
        "from dataclasses import dataclass\n\n\n"
        "@dataclass(frozen=True)\nclass Req:\n    pass\n\n\n"
        "@dataclass(frozen=True)\nclass Resp:\n    pass\n\n\n"
        "class Ping:\n    request = Req\n    response = Resp\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "device:\n  id: loopdev\n  display_name: Loop\n  audience: [internal]\n"
        "source_roots: [src]\n"
        "transports:\n  - id: loop\n    codec: dev.codec:LoopCodec\n    family: loopback\n"
        "commands:\n"
        "  - id: ping.check\n    handler: dev.handlers.ping:Ping\n    transports: [loop]\n"
        "    audience: [internal]\n",
        encoding="utf-8",
    )
    result = build_sdk(manifest, output_root=tmp_path / "out")
    registry = (result.package_path / "registry.py").read_text(encoding="utf-8")
    assert "LoopCodec" in registry
    assert "always_accepted_interpreter" in registry
    client = (result.package_path / "client.py").read_text(encoding="utf-8")
    assert "async def connect(" not in client  # loopback has no persistent identity


def test_typecheck_missing_mypy_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_cmd: list[str], **_kwargs: object) -> object:
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    with pytest.raises(BuildError, match="mypy is not installed"):
        build_mod._typecheck_generated_package(tmp_path, [tmp_path])


def test_typecheck_failure_reports_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        if "import mypy" in cmd:  # the availability probe succeeds
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="error: bad type", stderr="")

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    with pytest.raises(BuildError, match="failed --strict type checking"):
        build_mod._typecheck_generated_package(tmp_path, [tmp_path])
