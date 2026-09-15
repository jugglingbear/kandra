"""Integration tests for the M8 audience-pruning + vendoring build pipeline."""

from __future__ import annotations

import json
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from kandra.audience import AudienceError
from kandra.audit import audit_profile
from kandra.generator import BuildError, build_sdk
from kandra.leakage import LeakageError
from kandra_runtime import HttpRequest, HttpResponse

EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "pneumatic_bear_poker"
EXAMPLE_MANIFEST = EXAMPLE_DIR / "manifest.yaml"
EXAMPLE_SRC = EXAMPLE_DIR / "src"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cleanup_modules(snapshot: set[str]) -> None:
    for name in list(sys.modules):
        if name not in snapshot:
            del sys.modules[name]


def _write_profiles(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "custom_profiles.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path against the reference example
# ---------------------------------------------------------------------------


def test_internal_profile_vendors_full_tree(tmp_path: Path) -> None:
    result = build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path, profile="internal", clean=True)
    internal = result.package_path / "_internal"
    assert (internal / "__init__.py").is_file()
    assert (internal / "devices" / "pneumatic_bear_poker" / "handlers" / "poker.py").is_file()
    # Force-included (extra_include) module the static walker can't see is vendored.
    assert (internal / "devices" / "pneumatic_bear_poker" / "handlers" / "super_important.py").is_file()
    # Assets copied verbatim.
    assert (internal / "devices" / "pneumatic_bear_poker" / "assets" / "poke_profiles.json").is_file()


def test_partner_profile_prunes_internal_only_files(tmp_path: Path) -> None:
    result = build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path, profile="partner_woodland", clean=True)
    internal = result.package_path / "_internal"
    # Excluded bench tooling and internal-default files must be absent.
    assert not (internal / "devices" / "pneumatic_bear_poker" / "handlers" / "super_secret.py").exists()
    assert not (internal / "common" / "codecs" / "json.py").exists()
    # Granted partner files present.
    assert (internal / "devices" / "pneumatic_bear_poker" / "handlers" / "poker.py").is_file()


def test_partner_profile_output_has_no_forbidden_substrings(tmp_path: Path) -> None:
    result = build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path, profile="partner_woodland", clean=True)
    for path in result.package_path.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json"}:
            text = path.read_text(encoding="utf-8")
            assert "super_secret" not in text
            assert "dump_internal_registers" not in text


def test_vendored_registry_imports_point_at_internal(tmp_path: Path) -> None:
    result = build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path, profile="partner_woodland", clean=True)
    registry = (result.package_path / "registry.py").read_text(encoding="utf-8")
    assert "pneumatic_bear_poker_sdk._internal.devices.pneumatic_bear_poker.handlers.poker" in registry
    # No bare authoring-tree import survives.
    assert "from devices.pneumatic_bear_poker" not in registry


async def test_vendored_partner_sdk_dispatches_without_source_roots(tmp_path: Path) -> None:
    """The vendored SDK round-trips a command with the source roots OFF sys.path."""
    build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path, profile="partner_woodland", clean=True)
    out_root = tmp_path / "partner_woodland"

    snapshot = set(sys.modules)
    sys.path.insert(0, str(out_root))  # NOTE: EXAMPLE_SRC deliberately absent.
    try:
        from kandra_runtime import LoopbackTransport, open_transport
        from pneumatic_bear_poker_sdk import PneumaticBearPokerClient, TransportId
        from pneumatic_bear_poker_sdk._internal.devices.pneumatic_bear_poker.handlers.poker import (
            DeployRequest,
            DeployResponse,
        )

        def echo(req: HttpRequest) -> HttpResponse:
            body = json.loads((req.body or b"{}").decode("utf-8"))
            return HttpResponse(status=200, body=json.dumps({"delivered_psi": body["pressure_psi"]}).encode())

        transport: LoopbackTransport[HttpRequest, HttpResponse] = LoopbackTransport(echo)
        async with open_transport(transport) as t:
            client = PneumaticBearPokerClient(transports={TransportId.HTTP: t})
            result = await client.poker.deploy(DeployRequest(pressure_psi=21))
        assert result is not None
        assert result.accepted
        assert result.data == DeployResponse(delivered_psi=21)
    finally:
        with suppress(ValueError):
            sys.path.remove(str(out_root))
        _cleanup_modules(snapshot)


def test_provenance_records_profile_and_hashes(tmp_path: Path) -> None:
    result = build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path, profile="partner_woodland", clean=True)
    provenance = json.loads((result.package_path / "_generated_from.json").read_text(encoding="utf-8"))
    assert provenance["profile"] == "partner_woodland"
    assert len(provenance["manifest_sha256"]) == 64
    assert len(provenance["audience_profiles_sha256"]) == 64
    assert "generated_at" in provenance
    assert provenance["kandra_version"]


# ---------------------------------------------------------------------------
# Failure modes against the reference example (custom profiles files)
# ---------------------------------------------------------------------------


def test_unknown_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(AudienceError, match="unknown profile 'ghost'"):
        build_sdk(EXAMPLE_MANIFEST, output_root=tmp_path, profile="ghost", clean=True)


def test_no_surviving_commands_raises(tmp_path: Path) -> None:
    profiles = _write_profiles(
        tmp_path,
        "profiles:\n  public:\n    include_audience: [public]\n",
    )
    with pytest.raises(BuildError, match="no commands, attributes, or events survive audience pruning"):
        build_sdk(
            EXAMPLE_MANIFEST,
            output_root=tmp_path,
            profile="public",
            profiles_path=profiles,
            clean=True,
        )


def test_leakage_scan_fails_on_denied_substring(tmp_path: Path) -> None:
    # "Deploy" appears in the vendored poker handler; deny it to force a hit.
    profiles = _write_profiles(
        tmp_path,
        "profiles:\n"
        "  leaky:\n"
        "    include_audience: [internal, partner_woodland]\n"
        "    deny_substrings: [Deploy]\n",
    )
    with pytest.raises(LeakageError, match="forbidden substring"):
        build_sdk(
            EXAMPLE_MANIFEST,
            output_root=tmp_path,
            profile="leaky",
            profiles_path=profiles,
            clean=True,
        )


# ---------------------------------------------------------------------------
# Failure modes with a controlled mini-project
# ---------------------------------------------------------------------------


def _make_mini_project(
    root: Path,
    *,
    files_map: dict[str, list[str]],
    ping_header: str = "",
    handler_imports_sibling: bool = True,
) -> Path:
    """Create a minimal manifest + source tree + audience profiles under ``root``.

    Returns the manifest path. The single command ``ping.check`` is granted to
    ``[internal, partner]``; the ``partner`` profile includes only ``partner``.
    """
    src = root / "src"
    sibling_import = "from dev.helpers.extra import HELP\n" if handler_imports_sibling else ""
    sibling_use = "_ = HELP\n" if handler_imports_sibling else ""
    tree = {
        "dev/__init__.py": "",
        "dev/handlers/__init__.py": "",
        "dev/handlers/ping.py": (
            f"{ping_header}"
            "from __future__ import annotations\n\n"
            "from dataclasses import dataclass\n\n"
            f"{sibling_import}\n"
            "@dataclass(frozen=True)\n"
            "class PingRequest:\n    n: int\n\n\n"
            "@dataclass(frozen=True)\n"
            "class PingResponse:\n    n: int\n\n\n"
            "class Ping:\n    request = PingRequest\n    response = PingResponse\n\n\n"
            f"{sibling_use}"
        ),
        "dev/helpers/__init__.py": "",
        "dev/helpers/extra.py": "HELP = 1\n",
    }
    for rel, text in tree.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    manifest = root / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal, partner]\n"
        "source_roots: [src]\n"
        "transports:\n  - id: http\n    codec: k.c:C\n    family: http\n"
        "commands:\n"
        "  - id: ping.check\n"
        "    handler: dev.handlers.ping:Ping\n"
        "    transports: [http]\n"
        "    audience: [internal, partner]\n"
        "    http:\n      http:\n        method: POST\n        path: /ping\n",
        encoding="utf-8",
    )

    files_block = "".join(f"  {path}: {tags}\n" for path, tags in files_map.items())
    (root / "audience_profiles.yaml").write_text(
        "profiles:\n" "  partner:\n    include_audience: [partner]\n    deny_substrings: []\n" f"files:\n{files_block}",
        encoding="utf-8",
    )
    return manifest


def test_entry_file_dropped_raises(tmp_path: Path) -> None:
    # ping.py defaults to internal (not granted partner) but ping.check survives
    # pruning -> entry handler is required yet excluded.
    manifest = _make_mini_project(
        tmp_path,
        files_map={"src/dev/helpers/extra.py": ["internal", "partner"]},
        handler_imports_sibling=False,
    )
    with pytest.raises(BuildError, match="audience leak: entry module"):
        build_sdk(manifest, output_root=tmp_path / "out", profile="partner", clean=True)


def test_kept_file_importing_dropped_raises(tmp_path: Path) -> None:
    # ping.py granted partner (kept) but its sibling extra.py defaults internal.
    manifest = _make_mini_project(
        tmp_path,
        files_map={
            "src/dev/__init__.py": ["internal", "partner"],
            "src/dev/handlers/__init__.py": ["internal", "partner"],
            "src/dev/handlers/ping.py": ["internal", "partner"],
            "src/dev/helpers/__init__.py": ["internal", "partner"],
            # extra.py intentionally omitted -> defaults internal -> dropped.
        },
        handler_imports_sibling=True,
    )
    with pytest.raises(BuildError, match="imports .* which is excluded"):
        build_sdk(manifest, output_root=tmp_path / "out", profile="partner", clean=True)


def test_narrowing_header_drops_entry_file_raises(tmp_path: Path) -> None:
    # ping.py is granted partner in YAML but narrows itself to internal via header.
    manifest = _make_mini_project(
        tmp_path,
        files_map={
            "src/dev/__init__.py": ["internal", "partner"],
            "src/dev/handlers/__init__.py": ["internal", "partner"],
            "src/dev/handlers/ping.py": ["internal", "partner"],
            "src/dev/helpers/__init__.py": ["internal", "partner"],
            "src/dev/helpers/extra.py": ["internal", "partner"],
        },
        ping_header="# kandra-audience: internal\n",
        handler_imports_sibling=True,
    )
    with pytest.raises(BuildError, match="audience leak: entry module"):
        build_sdk(manifest, output_root=tmp_path / "out", profile="partner", clean=True)


# ---------------------------------------------------------------------------
# audit_profile
# ---------------------------------------------------------------------------


def test_audit_reports_included_and_pruned() -> None:
    report = audit_profile(EXAMPLE_MANIFEST, "partner_woodland")
    by_path = {row.rel_path: row for row in report.files}
    assert by_path["src/devices/pneumatic_bear_poker/handlers/poker.py"].included is True
    assert by_path["src/devices/pneumatic_bear_poker/handlers/super_secret.py"].included is False


def test_audit_detects_stale_path(tmp_path: Path) -> None:
    profiles = _write_profiles(
        tmp_path,
        "profiles:\n"
        "  internal:\n    include_audience: [internal, partner_woodland]\n"
        "files:\n"
        "  src/does/not/exist.py: [internal]\n",
    )
    report = audit_profile(EXAMPLE_MANIFEST, "internal", profiles_path=profiles)
    assert report.stale_paths == ("src/does/not/exist.py",)
