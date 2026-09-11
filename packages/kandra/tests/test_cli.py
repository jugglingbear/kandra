"""Tests for :mod:`kandra.cli` command dispatch and error exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kandra import cli as cli_mod
from kandra.cli import main
from kandra.scaffold import ScaffoldError

EXAMPLE_MANIFEST = Path(__file__).resolve().parents[3] / "examples" / "pneumatic_bear_poker" / "manifest.yaml"
_SCAFFOLD_FIXTURE = Path(__file__).parent / "fixtures" / "scaffold_minimal.yaml"


def test_schema_command_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["schema"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["title"] == "Manifest"


def test_validate_ok(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["validate", str(EXAMPLE_MANIFEST)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK:" in out
    # The example declares one attribute; the summary surfaces it.
    assert "attributes=1" in out


def test_validate_bad_manifest_returns_1(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 1\n", encoding="utf-8")  # missing everything else
    rc = main(["validate", str(bad)])
    assert rc == 1


def test_build_bad_manifest_returns_1(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a valid manifest\n", encoding="utf-8")
    rc = main(["build", str(bad), "--output-dir", str(tmp_path / "out")])
    assert rc == 1


def test_create_sdk_non_interactive_requires_answers(tmp_path: Path) -> None:
    rc = main(["create-sdk", str(tmp_path / "new"), "--non-interactive"])
    assert rc == 2


def test_create_sdk_rejects_nonempty_target(tmp_path: Path) -> None:
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("busy\n", encoding="utf-8")
    rc = main(["create-sdk", str(target)])
    assert rc == 1


def test_create_sdk_invalid_answers_file_returns_1(tmp_path: Path) -> None:
    answers = tmp_path / "answers.yaml"
    answers.write_text("{}\n", encoding="utf-8")  # valid YAML, fails Answers validation
    rc = main(["create-sdk", str(tmp_path / "new"), "--non-interactive", "--answers", str(answers)])
    assert rc == 1


def test_create_sdk_keyboard_interrupt_returns_130(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_path: Path) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "run_wizard", boom)
    rc = main(["create-sdk", str(tmp_path / "new")])
    assert rc == 130


def test_create_sdk_render_error_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_answers: object, _target: Path) -> object:
        raise ScaffoldError("render kaboom")

    monkeypatch.setattr(cli_mod, "render", boom)
    rc = main(["create-sdk", str(tmp_path / "new"), "--non-interactive", "--answers", str(_SCAFFOLD_FIXTURE)])
    assert rc == 1
