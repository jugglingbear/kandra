"""Smoke tests for ``kandra create-sdk`` — template drift mitigation.

If these tests fail, it means a change to kandra core (manifest schema,
loader behavior, CLI flags) silently broke the scaffold templates. Fix
the templates rather than disabling the test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kandra.cli import main as cli_main
from kandra.loader import load_manifest
from kandra.scaffold import Answers, TransportAnswer, render
from kandra.scaffold.wizard import _default_project_name

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _minimal_answers() -> Answers:
    """Build the minimal Answers used by drift smoke tests."""
    return Answers.from_partial(
        project_name="Test Device SDK",
        transports=[
            TransportAnswer(family="ble", wire_format="json", service_uuid="abcdef01-2345-6789-abcd-ef0123456789"),
            TransportAnswer(family="http", wire_format="json", base_url="http://192.168.1.1:8080"),
        ],
    )


def test_render_minimal_to_empty_dir(tmp_path: Path) -> None:
    """Scaffold renders into an empty directory and produces all expected files."""
    target = tmp_path / "device"
    answers = _minimal_answers()
    result = render(answers, target)

    assert result.target == target.resolve()
    expected_files = {
        "pyproject.toml",
        "README.md",
        "LICENSE",
        ".gitignore",
        "Makefile",
        "kandra.yaml",
        "src/test_device_sdk/__init__.py",
        "src/test_device_sdk/handlers/__init__.py",
        "src/test_device_sdk/handlers/ping.py",
        "src/test_device_sdk/codecs/__init__.py",
        "src/test_device_sdk/codecs/json_codec.py",
        "src/test_device_sdk/transports/__init__.py",
        "src/test_device_sdk/transports/ble.py",
        "src/test_device_sdk/transports/http.py",
        "tests/test_smoke.py",
        "examples/connect.py",
    }
    actual = {str(p.relative_to(target.resolve())) for p in result.files}
    assert expected_files == actual


def test_rendered_manifest_validates(tmp_path: Path) -> None:
    """The generated ``kandra.yaml`` passes ``load_manifest`` (= ``kandra validate``)."""
    target = tmp_path / "device"
    render(_minimal_answers(), target)
    manifest = load_manifest(target / "kandra.yaml")
    assert manifest.device.id == "test_device"
    assert len(manifest.transports) == 2
    assert len(manifest.commands) == 1


def test_render_refuses_non_empty_dir(tmp_path: Path) -> None:
    """Scaffold refuses to clobber an existing non-empty directory."""
    target = tmp_path / "device"
    target.mkdir()
    (target / "existing.txt").write_text("don't touch me", encoding="utf-8")
    from kandra.scaffold import ScaffoldError

    with pytest.raises(ScaffoldError, match="non-empty"):
        render(_minimal_answers(), target)
    # The pre-existing file is still there.
    assert (target / "existing.txt").read_text() == "don't touch me"


def test_render_into_empty_existing_dir(tmp_path: Path) -> None:
    """Empty pre-existing directory is fine."""
    target = tmp_path / "device"
    target.mkdir()
    render(_minimal_answers(), target)
    assert (target / "kandra.yaml").is_file()


def test_cli_non_interactive_end_to_end(tmp_path: Path) -> None:
    """``kandra create-sdk --non-interactive --answers FIX TARGET`` works end-to-end."""
    fixture = FIXTURE_DIR / "scaffold_minimal.yaml"
    target = tmp_path / "device"
    rc = cli_main(["create-sdk", str(target), "--non-interactive", "--answers", str(fixture)])
    assert rc == 0
    assert (target / "kandra.yaml").is_file()
    # And the resulting manifest validates via the CLI too.
    rc2 = cli_main(["validate", str(target / "kandra.yaml")])
    assert rc2 == 0


@pytest.mark.skipif(shutil.which("poetry") is None, reason="poetry not installed")
def test_generated_pytest_smoke_passes(tmp_path: Path) -> None:
    """The generated ``tests/test_smoke.py`` passes when run with the current kandra install.

    Uses ``python -m pytest`` directly (no fresh ``poetry install``) — both the
    scaffold and the host venv share the same kandra. This catches drift in
    the smoke test template without paying for a real Poetry environment.
    """
    target = tmp_path / "device"
    render(_minimal_answers(), target)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(target / "tests")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"generated test_smoke.py failed:\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (Path("/tmp/test-sdk"), "Test SDK"),
        (Path("/tmp/test_sdk"), "Test SDK"),
        (Path("/tmp/my-cool-device-sdk"), "My Cool Device SDK"),
        (Path("/tmp/my_cool_thing"), "My Cool Thing"),
        (Path("/tmp/widget"), "Widget"),
        (Path("/tmp/SDK"), "My Device SDK"),  # nothing left after strip
        (None, "My Device SDK"),
    ],
)
def test_default_project_name(path: Path | None, expected: str) -> None:
    """`_default_project_name` derives sensible defaults from the target path."""
    assert _default_project_name(path) == expected


@pytest.mark.parametrize(
    ("slug", "expected_device_id"),
    [
        ("test-sdk", "test"),
        ("my-cool-device-sdk", "my_cool_device"),
        ("thermostat", "thermostat"),
        ("x-sdk", "x"),
        ("sdk", "sdk"),  # nothing left after strip — keep original
    ],
)
def test_device_id_strips_sdk_suffix(slug: str, expected_device_id: str) -> None:
    """device_id drops a trailing ``sdk`` so the built package isn't ``foo_sdk_sdk``."""
    answers = Answers.from_partial(
        project_name="Whatever",
        slug=slug,
        transports=[TransportAnswer(family="ble")],
    )
    assert answers.device_id == expected_device_id
