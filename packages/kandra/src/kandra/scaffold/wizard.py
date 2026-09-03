"""Interactive (questionary) and file-based wizard for collecting :class:`Answers`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import questionary
from ruamel.yaml import YAML

from kandra.scaffold.answers import Answers, TransportAnswer, _deviceize, _pythonize, _slugify

_yaml = YAML(typ="safe", pure=True)

# Reasonable BLE placeholder service UUID; user is told to replace it.
_PLACEHOLDER_BLE_UUID = "00000000-0000-1000-8000-00805f9b34fb"
_DEFAULT_HTTP_BASE_URL = "http://192.168.1.1:8080"


def load_answers(path: Path) -> Answers:
    """Load an :class:`Answers` from a YAML file (for ``--non-interactive``)."""
    raw = _yaml.load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return Answers.model_validate(raw)


def _default_project_name(target: Path | None) -> str:
    """Derive a reasonable project-name default from the target directory.

    ``/tmp/test-sdk`` → ``Test SDK``; ``~/my_cool_thing`` → ``My Cool Thing``.
    Strips a trailing ``-sdk`` / ``_sdk`` segment so it isn't doubled-up
    when the wizard reattaches it later in slugs and package names. Falls
    back to ``My Device SDK`` when ``target`` is None or unusable.
    """
    if target is None:
        return "My Device SDK"
    stem = target.name.strip()
    if not stem:
        return "My Device SDK"
    # Strip a trailing -sdk / _sdk / sdk so we can append " SDK" cleanly
    # without doubling it up.
    stripped, n = re.subn(r"[-_]?sdk$", "", stem, flags=re.IGNORECASE)
    if not stripped:
        return "My Device SDK"
    words = [w for w in re.split(r"[-_\s]+", stripped) if w]
    if not words:
        return "My Device SDK"
    name = " ".join(w.capitalize() for w in words)
    return f"{name} SDK" if n else name


# Placeholder display name when we can't derive a meaningful one from the
# project name (e.g. user accepted the "My Device SDK" default).
_PLACEHOLDER_DISPLAY_NAME = "Pneumatic Bear Poker"


def _default_display_name(project_name: str) -> str:
    """Derive a device display name from the project name.

    Strips a trailing ``SDK`` token (the SDK is not the device). Falls back
    to a whimsical placeholder when nothing meaningful remains — the user
    will almost certainly overwrite it, which is the point.
    """
    stripped = re.sub(r"\s*SDK\s*$", "", project_name, flags=re.IGNORECASE).strip()
    if not stripped or stripped.lower() in {"my device", "device"}:
        return _PLACEHOLDER_DISPLAY_NAME
    return stripped


def run_wizard(target: Path | None = None) -> Answers:
    """Interactively collect answers from the user, returning an :class:`Answers`.

    If ``target`` is supplied, its basename is used to derive a smart
    default for the project name (e.g. ``/tmp/test-sdk`` → ``Test SDK``).

    Aborts with ``KeyboardInterrupt`` if the user cancels (Ctrl+C). Callers
    should catch that and exit cleanly.
    """
    project_name = _ask_text(
        "Project name",
        default=_default_project_name(target),
        instruction="(human-readable; used in pyproject.toml / README)",
    )
    suggested_slug = _slugify(project_name)
    slug = _ask_text(
        "Project slug",
        default=suggested_slug,
        validate=_validate_slug,
        instruction="(lowercase, hyphenated; used as the directory + PyPI name)",
    )
    suggested_pkg = _pythonize(slug)
    package_name = _ask_text(
        "Python package name",
        default=suggested_pkg,
        validate=_validate_ident,
        instruction="(snake_case; what users `import`)",
    )

    display_name = _ask_text(
        "Device display name",
        default=_default_display_name(project_name),
        instruction="(shown in logs / docs)",
    )
    has_codename = questionary.confirm(
        "Does your device have a separate internal codename (e.g. 'atium')?",
        default=False,
    ).ask()
    if has_codename is None:
        raise KeyboardInterrupt
    codename: str | None = None
    if has_codename:
        codename = _ask_text(
            "Device codename",
            default=_deviceize(slug),
            validate=_validate_ident,
            instruction="(snake_case; used as manifest device.id)",
        )
    device_id = codename or _deviceize(slug)

    transport_families = _ask_checkbox(
        "Which transports does your device support?",
        choices=[
            questionary.Choice("ble  — Bluetooth Low Energy", value="ble"),
            questionary.Choice("http — Wi-Fi / Ethernet HTTP API", value="http"),
        ],
        instruction="(<space> to toggle; pick at least one)",
        validate=_validate_at_least_one,
    )

    transports: list[TransportAnswer] = []
    previous_wire: str = "json"
    for fam in transport_families:
        transports.append(_ask_transport(fam, default_wire=previous_wire))
        previous_wire = transports[-1].wire_format

    dependency_source = _ask_dependency_source()

    answers = Answers.from_partial(
        project_name=project_name,
        slug=slug,
        package_name=package_name,
        device_id=device_id,
        display_name=display_name,
        codename=codename,
        transports=transports,
        dependency_source=dependency_source,  # type: ignore[arg-type]
    )

    _show_summary(answers)
    confirm = questionary.confirm("Generate the project?", default=True).ask()
    if not confirm:
        raise KeyboardInterrupt
    return answers


# ---------------------------------------------------------------------------
# Per-transport sub-wizard
# ---------------------------------------------------------------------------


def _ask_transport(family: str, *, default_wire: str) -> TransportAnswer:
    questionary.print(f"\n— Configuring transport: {family} —", style="bold")
    questionary.print(
        "  How are request/response bytes encoded on this transport?",
        style="italic",
    )
    wire = _ask_select(
        "  Wire format",
        choices=[
            questionary.Choice("json     — human-readable text (e.g. {\"power\": true})", value="json"),
            questionary.Choice("protobuf — compact binary, schema-defined (.proto files)", value="protobuf"),
            questionary.Choice("raw      — opaque bytes, you handle parsing yourself", value="raw"),
        ],
        default=default_wire,
    )
    if family == "ble":
        service_uuid = _ask_text(
            "  BLE service UUID",
            default=_PLACEHOLDER_BLE_UUID,
            instruction="(replace with your device's primary service UUID later)",
        )
        name_prefix_raw = _ask_text(
            "  BLE advertising name prefix (blank to skip)",
            default="",
        )
        return TransportAnswer(
            family="ble",
            wire_format=wire,  # type: ignore[arg-type]
            service_uuid=service_uuid,
            name_prefix=name_prefix_raw or None,
        )
    if family == "http":
        base_url = _ask_text(
            "  HTTP base URL",
            default=_DEFAULT_HTTP_BASE_URL,
            instruction="(scheme + host + port)",
        )
        return TransportAnswer(
            family="http",
            wire_format=wire,  # type: ignore[arg-type]
            base_url=base_url,
        )
    raise ValueError(f"unsupported transport family: {family!r}")


def _ask_dependency_source() -> str:
    """Ask how the generated project should depend on kandra / kandra-runtime."""
    questionary.print(
        "\nHow should the new project depend on kandra?",
        style="bold",
    )
    questionary.print(
        "  kandra isn't on PyPI yet, so a local path dependency is the only\n"
        "  option that installs today. Pick PyPI only if you'll publish later.",
        style="italic",
    )
    return _ask_select(
        "  Dependency source",
        choices=[
            questionary.Choice(
                "local — editable path dep on this kandra checkout (installs now)",
                value="local",
            ),
            questionary.Choice(
                "pypi  — version constraint for a future published release",
                value="pypi",
            ),
        ],
        default="local",
    )


# ---------------------------------------------------------------------------
# Questionary helpers — thin wrappers that raise KeyboardInterrupt on cancel
# ---------------------------------------------------------------------------


def _ask_text(
    message: str,
    *,
    default: str = "",
    validate: Any = None,
    instruction: str | None = None,
) -> str:
    answer = questionary.text(
        message,
        default=default,
        validate=validate,
        instruction=instruction,
    ).ask()
    if answer is None:
        raise KeyboardInterrupt
    return str(answer)


def _ask_select(message: str, *, choices: list[Any], default: str | None = None) -> str:
    answer = questionary.select(message, choices=choices, default=default).ask()
    if answer is None:
        raise KeyboardInterrupt
    return str(answer)


def _ask_checkbox(
    message: str,
    *,
    choices: list[Any],
    instruction: str | None = None,
    validate: Any = None,
) -> list[str]:
    answer = questionary.checkbox(
        message,
        choices=choices,
        instruction=instruction,
        validate=validate,
    ).ask()
    if answer is None:
        raise KeyboardInterrupt
    return list(answer)


def _validate_at_least_one(selected: list[Any]) -> bool | str:
    """Require at least one checkbox selection (questionary validator)."""
    if not selected:
        return "pick at least one (use <space> to toggle)"
    return True


def _validate_slug(value: str) -> bool | str:
    if not re.match(r"^[a-z][a-z0-9-]*$", value):
        return "use lowercase letters, digits, hyphens; must start with a letter"
    return True


def _validate_ident(value: str) -> bool | str:
    if not re.match(r"^[a-z][a-z0-9_]*$", value):
        return "use lowercase letters, digits, underscores; must start with a letter"
    return True


def _show_summary(answers: Answers) -> None:
    questionary.print("\n=== Summary ===", style="bold")
    questionary.print(f"  Project       : {answers.project_name}")
    questionary.print(f"  Slug          : {answers.slug}")
    questionary.print(f"  Package       : {answers.package_name}")
    questionary.print(f"  Device id     : {answers.device_id}")
    questionary.print(f"  Display name  : {answers.display_name}")
    if answers.codename:
        questionary.print(f"  Codename      : {answers.codename}")
    questionary.print(f"  Audience      : {', '.join(answers.audience)}")
    questionary.print(f"  Python        : ^{answers.python_version}")
    questionary.print(f"  Deps          : {answers.dependency_source}")
    questionary.print("  Transports    :")
    for t in answers.transports:
        details: list[str] = []
        if t.family == "ble":
            details.append(f"uuid={t.service_uuid}")
            if t.name_prefix:
                details.append(f"name_prefix={t.name_prefix}")
        elif t.family == "http":
            details.append(f"base_url={t.base_url}")
        questionary.print(f"    - {t.family} ({t.wire_format}) — {', '.join(details)}")
    questionary.print("")
