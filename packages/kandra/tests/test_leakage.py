"""Unit tests for :mod:`kandra.leakage` — the forbidden-substring scanner."""

from __future__ import annotations

from pathlib import Path

import pytest
from kandra.leakage import LeakageError, assert_no_leakage, scan_tree


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_clean_tree_returns_no_hits(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "sub/b.md", "# Title\nall good\n")
    assert scan_tree(tmp_path, ["skunkworks"]) == []


def test_scan_empty_denylist_returns_no_hits(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "skunkworks lives here\n")
    assert scan_tree(tmp_path, []) == []


def test_scan_ignores_empty_denylist_entries(tmp_path: Path) -> None:
    # Empty strings would match every line; they must be filtered out.
    _write(tmp_path, "a.py", "anything\n")
    assert scan_tree(tmp_path, ["", ""]) == []


def test_scan_finds_single_hit(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "clean\nsecret = 'skunkworks'\n")
    hits = scan_tree(tmp_path, ["skunkworks"])
    assert len(hits) == 1
    assert hits[0].line_number == 2
    assert hits[0].substring == "skunkworks"
    assert "skunkworks" in hits[0].line


def test_scan_finds_hits_across_files_and_substrings(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "email = '@example.internal'\n")
    _write(tmp_path, "meta/_generated_from.json", '{\n  "path": "src/devices/_unreleased/x"\n}\n')
    hits = scan_tree(tmp_path, ["@example.internal", "src/devices/_unreleased/"])
    substrings = {h.substring for h in hits}
    assert substrings == {"@example.internal", "src/devices/_unreleased/"}


def test_scan_skips_binary_files(tmp_path: Path) -> None:
    # A non-UTF-8 byte sequence must not crash the scan and must be skipped.
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\xff\xfe skunkworks \x00\x80")
    assert scan_tree(tmp_path, ["skunkworks"]) == []


def test_scan_skips_pycache(tmp_path: Path) -> None:
    _write(tmp_path, "__pycache__/mod.cpython-312.pyc.txt", "skunkworks\n")
    assert scan_tree(tmp_path, ["skunkworks"]) == []


def test_scan_is_case_sensitive(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "SKUNKWORKS\n")
    assert scan_tree(tmp_path, ["skunkworks"]) == []


def test_assert_no_leakage_passes_on_clean_tree(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    assert_no_leakage(tmp_path, ["skunkworks"])  # no raise


def test_assert_no_leakage_raises_with_report(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/a.py", "token = 'skunkworks'\n")
    with pytest.raises(LeakageError) as excinfo:
        assert_no_leakage(tmp_path, ["skunkworks"], base=tmp_path)
    message = str(excinfo.value)
    assert "1 forbidden substring match" in message
    assert "pkg/a.py:1" in message


def test_assert_no_leakage_reports_relative_to_base(tmp_path: Path) -> None:
    pkg = tmp_path / "dist" / "partner"
    _write(pkg, "client.py", "author = '@example.internal'\n")
    with pytest.raises(LeakageError, match=r"client\.py:1"):
        assert_no_leakage(pkg, ["@example.internal"], base=pkg)


def test_assert_no_leakage_falls_back_to_absolute_path(tmp_path: Path) -> None:
    # When a hit is not under ``base``, the report shows the absolute path.
    scanned = tmp_path / "scanned"
    hit_file = _write(scanned, "a.py", "skunkworks\n")
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    with pytest.raises(LeakageError) as excinfo:
        assert_no_leakage(scanned, ["skunkworks"], base=unrelated)
    assert str(hit_file) in str(excinfo.value)
