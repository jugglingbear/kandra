"""Tests for the interactive scaffold wizard (driven via monkeypatched prompts)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from kandra.scaffold import wizard
from kandra.scaffold.wizard import (
    _ask_checkbox,
    _ask_select,
    _ask_text,
    _default_display_name,
    _default_project_name,
    _validate_at_least_one,
    _validate_ident,
    _validate_slug,
    load_answers,
    run_wizard,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_load_answers_non_mapping_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a YAML mapping"):
        load_answers(path)


def test_default_project_name_none() -> None:
    assert _default_project_name(None) == "My Device SDK"


def test_default_project_name_empty_stem() -> None:
    assert _default_project_name(Path("/")) == "My Device SDK"


def test_default_project_name_only_separators() -> None:
    assert _default_project_name(Path("--")) == "My Device SDK"


def test_default_project_name_strips_sdk_suffix() -> None:
    assert _default_project_name(Path("/tmp/test-sdk")) == "Test SDK"


def test_default_project_name_from_words() -> None:
    assert _default_project_name(Path("/x/my_cool_thing")) == "My Cool Thing"


def test_default_display_name_strips_sdk() -> None:
    assert _default_display_name("Camera SDK") == "Camera"


def test_default_display_name_falls_back_to_placeholder() -> None:
    assert _default_display_name("My Device SDK") == "Pneumatic Bear Poker"


def test_validators() -> None:
    assert isinstance(_validate_at_least_one([]), str)  # error message
    assert _validate_at_least_one(["ble"]) is True
    assert _validate_slug("good-slug") is True
    assert isinstance(_validate_slug("Bad_Slug"), str)
    assert _validate_ident("good_ident") is True
    assert isinstance(_validate_ident("Bad-Ident"), str)


# ---------------------------------------------------------------------------
# Questionary wrapper seams
# ---------------------------------------------------------------------------


class _FakeQ:
    def __init__(self, value: Any) -> None:
        self._value = value

    def ask(self) -> Any:
        return self._value


def test_ask_text_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.questionary, "text", lambda *a, **k: _FakeQ("hello"))
    assert _ask_text("Q") == "hello"


def test_ask_text_cancel_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.questionary, "text", lambda *a, **k: _FakeQ(None))
    with pytest.raises(KeyboardInterrupt):
        _ask_text("Q")


def test_ask_select_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.questionary, "select", lambda *a, **k: _FakeQ("json"))
    assert _ask_select("Q", choices=[]) == "json"


def test_ask_select_cancel_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.questionary, "select", lambda *a, **k: _FakeQ(None))
    with pytest.raises(KeyboardInterrupt):
        _ask_select("Q", choices=[])


def test_ask_checkbox_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _FakeQ(["ble"]))
    assert _ask_checkbox("Q", choices=[]) == ["ble"]


def test_ask_checkbox_cancel_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.questionary, "checkbox", lambda *a, **k: _FakeQ(None))
    with pytest.raises(KeyboardInterrupt):
        _ask_checkbox("Q", choices=[])


# ---------------------------------------------------------------------------
# Full run_wizard drive
# ---------------------------------------------------------------------------


def _install_prompts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    families: list[str],
    has_codename: bool | None,
    generate: bool,
    ble_name_prefix: str = "",
) -> None:
    text_routes = {
        "Project name": "Test Device",
        "Project slug": "test-device",
        "Python package name": "test_device",
        "Device display name": "Test Device",
        "Device codename": "atium",
        "BLE service UUID": "abcdef01-2345-6789-abcd-ef0123456789",
        "advertising name prefix": ble_name_prefix,
        "HTTP base URL": "http://192.168.1.1:8080",
    }

    def fake_text(message: str, *, default: str = "", validate: Any = None, instruction: Any = None) -> str:
        for key, value in text_routes.items():
            if key in message:
                return value
        return default

    def fake_select(message: str, *, choices: Any, default: Any = None) -> str:
        if "Wire format" in message:
            return "json"
        if "Dependency source" in message:
            return "local"
        return default or "local"

    def fake_checkbox(message: str, *, choices: Any, instruction: Any = None, validate: Any = None) -> list[str]:
        return families

    def fake_confirm(message: str, default: bool = False) -> _FakeQ:
        return _FakeQ(has_codename if "codename" in message else generate)

    monkeypatch.setattr(wizard, "_ask_text", fake_text)
    monkeypatch.setattr(wizard, "_ask_select", fake_select)
    monkeypatch.setattr(wizard, "_ask_checkbox", fake_checkbox)
    monkeypatch.setattr(wizard.questionary, "confirm", fake_confirm)
    monkeypatch.setattr(wizard.questionary, "print", lambda *a, **k: None)


def test_run_wizard_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_prompts(monkeypatch, families=["ble", "http"], has_codename=False, generate=True)
    answers = run_wizard(Path("/tmp/test-device"))
    assert answers.package_name == "test_device"
    assert {t.family for t in answers.transports} == {"ble", "http"}
    assert answers.codename is None
    assert answers.dependency_source == "local"


def test_run_wizard_with_codename(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_prompts(monkeypatch, families=["ble"], has_codename=True, generate=True)
    answers = run_wizard(Path("/tmp/test-device"))
    assert answers.codename == "atium"
    assert answers.device_id == "atium"


def test_run_wizard_abort_on_decline(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_prompts(monkeypatch, families=["http"], has_codename=False, generate=False)
    with pytest.raises(KeyboardInterrupt):
        run_wizard(Path("/tmp/test-device"))


def test_run_wizard_abort_on_codename_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    # The codename confirm returning None (Ctrl+C) aborts the wizard.
    _install_prompts(monkeypatch, families=["ble"], has_codename=None, generate=True)
    with pytest.raises(KeyboardInterrupt):
        run_wizard(Path("/tmp/test-device"))


def test_run_wizard_ble_name_prefix_in_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_prompts(monkeypatch, families=["ble"], has_codename=False, generate=True, ble_name_prefix="BearPoker-")
    answers = run_wizard(Path("/tmp/test-device"))
    ble = next(t for t in answers.transports if t.family == "ble")
    assert ble.name_prefix == "BearPoker-"


def test_ask_transport_unsupported_family_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard, "_ask_select", lambda *a, **k: "json")
    monkeypatch.setattr(wizard.questionary, "print", lambda *a, **k: None)
    with pytest.raises(ValueError, match="unsupported transport family"):
        wizard._ask_transport("serial", default_wire="json")
