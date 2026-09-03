"""Tests for the ``kandra build`` CLI options (``--output-dir`` / ``--clean``)."""

from __future__ import annotations

from pathlib import Path

from kandra.cli import main
from kandra.generator import build_sdk

EXAMPLE_MANIFEST = Path(__file__).resolve().parents[3] / "examples" / "pneumatic_bear_poker" / "manifest.yaml"


def test_build_writes_to_output_dir(tmp_path: Path) -> None:
    """``--output-dir`` redirects the SDK package root."""
    rc = main(["build", str(EXAMPLE_MANIFEST), "--output-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "pneumatic_bear_poker_sdk" / "client.py").is_file()


def test_build_out_alias_still_works(tmp_path: Path) -> None:
    """``--out`` is preserved as a back-compat alias for ``--output-dir``."""
    rc = main(["build", str(EXAMPLE_MANIFEST), "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "pneumatic_bear_poker_sdk" / "client.py").is_file()


def test_build_clean_removes_stale_files(tmp_path: Path) -> None:
    """``--clean`` wipes the target package dir so removed files don't linger."""
    # First build: produce the SDK.
    main(["build", str(EXAMPLE_MANIFEST), "--output-dir", str(tmp_path)])
    pkg = tmp_path / "pneumatic_bear_poker_sdk"
    stale = pkg / "_stale_from_prior_build.py"
    stale.write_text("# left behind by an earlier generator version\n", encoding="utf-8")
    assert stale.exists()

    # Second build with --clean: stale file must be gone.
    rc = main(["build", str(EXAMPLE_MANIFEST), "--output-dir", str(tmp_path), "--clean"])
    assert rc == 0
    assert (pkg / "client.py").is_file()
    assert not stale.exists()


def test_build_without_clean_preserves_extra_files(tmp_path: Path) -> None:
    """Without ``--clean`` the generator is additive: unrelated files are kept."""
    main(["build", str(EXAMPLE_MANIFEST), "--output-dir", str(tmp_path)])
    pkg = tmp_path / "pneumatic_bear_poker_sdk"
    leftover = pkg / "_user_added.py"
    leftover.write_text("# user added this\n", encoding="utf-8")

    rc = main(["build", str(EXAMPLE_MANIFEST), "--output-dir", str(tmp_path)])
    assert rc == 0
    assert leftover.exists()


def test_build_sdk_clean_param_on_nonexistent_dir(tmp_path: Path) -> None:
    """``clean=True`` is safe when the target doesn't exist yet (first build)."""
    target = tmp_path / "fresh"
    result = build_sdk(EXAMPLE_MANIFEST, output_root=target, clean=True)
    assert result.package_path.is_dir()
    assert (result.package_path / "client.py").is_file()
