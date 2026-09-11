"""Unit tests for :mod:`kandra.audit` — report formatting and dedup."""

from __future__ import annotations

from pathlib import Path

from kandra.audit import AuditReport, FileAudit, audit_profile, format_report


def test_format_report_marks_included_and_pruned() -> None:
    report = AuditReport(
        profile="partner",
        include_audience=("partner",),
        files=(
            FileAudit(rel_path="src/a.py", effective=frozenset({"internal", "partner"}), included=True),
            FileAudit(rel_path="src/b.py", effective=frozenset({"internal"}), included=False),
        ),
        stale_paths=(),
    )
    out = format_report(report)
    assert "audit profile=partner" in out
    assert "+ src/a.py" in out
    assert "- src/b.py" in out
    assert "stale" not in out


def test_format_report_lists_stale_paths() -> None:
    report = AuditReport(
        profile="internal",
        include_audience=("internal",),
        files=(),
        stale_paths=("src/gone.py",),
    )
    out = format_report(report)
    assert "stale files-map entries" in out
    assert "! src/gone.py" in out


def _mini_manifest_with_overlapping_roots(root: Path) -> Path:
    src = root / "src"
    (src / "dev").mkdir(parents=True)
    (src / "dev" / "__init__.py").write_text("", encoding="utf-8")
    (src / "dev" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    manifest = root / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "device:\n  id: mini\n  display_name: Mini\n  audience: [internal]\n"
        "source_roots: [src, src/dev]\n"  # overlapping on purpose
        "transports:\n  - id: http\n    adapter: k.a:A\n    codec: k.c:C\n    family: http\n"
        "commands:\n"
        "  - id: ping.check\n    handler: dev.mod:X\n    transports: [http]\n"
        "    audience: [internal]\n    http:\n      http:\n        method: GET\n        path: /\n",
        encoding="utf-8",
    )
    (root / "audience_profiles.yaml").write_text(
        "profiles:\n  internal:\n    include_audience: [internal]\n",
        encoding="utf-8",
    )
    return manifest


def test_audit_dedups_files_from_overlapping_roots(tmp_path: Path) -> None:
    manifest = _mini_manifest_with_overlapping_roots(tmp_path)
    report = audit_profile(manifest, "internal")
    rel_paths = [row.rel_path for row in report.files]
    assert len(rel_paths) == len(set(rel_paths))  # no duplicates despite overlap
    assert "src/dev/mod.py" in rel_paths
