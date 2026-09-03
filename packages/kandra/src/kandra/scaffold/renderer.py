"""Render an :class:`Answers` to a target directory via Jinja2 templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

import kandra
from kandra.scaffold.answers import Answers

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class ScaffoldError(Exception):
    """Raised when scaffolding cannot proceed (target exists, IO error, etc.)."""


def ensure_target_available(target: Path) -> None:
    """Raise :class:`ScaffoldError` if ``target`` exists and is non-empty.

    Scaffolding is a one-shot generator that never overwrites existing
    work, so callers should run this check *before* doing any expensive or
    interactive work (e.g. the wizard) to fail fast rather than asking the
    user a pile of questions only to refuse at the end.
    """
    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        raise ScaffoldError(
            f"refusing to scaffold into non-empty directory {target}; "
            "remove it (or pass a fresh path) and try again"
        )


@dataclass(frozen=True)
class RenderResult:
    """Files written by :func:`render`."""

    target: Path
    files: tuple[Path, ...]


def render(answers: Answers, target: Path) -> RenderResult:
    """Render the scaffold for ``answers`` into ``target``.

    Refuses to overwrite an existing non-empty ``target`` directory.
    Creates ``target`` if it doesn't exist.
    """
    target = target.resolve()
    ensure_target_available(target)
    target.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
        autoescape=False,
    )
    context = _build_context(answers)

    written: list[Path] = []
    for template_rel, dest_rel in _plan(answers):
        dest = target / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmpl = env.get_template(template_rel)
        dest.write_text(tmpl.render(**context), encoding="utf-8")
        written.append(dest)

    return RenderResult(target=target, files=tuple(written))


def _plan(answers: Answers) -> list[tuple[str, str]]:
    """Return the list of (template-relative-path, destination-relative-path) pairs."""
    pkg = answers.package_name
    plan: list[tuple[str, str]] = [
        ("pyproject.toml.jinja", "pyproject.toml"),
        ("README.md.jinja", "README.md"),
        ("LICENSE.jinja", "LICENSE"),
        ("gitignore.jinja", ".gitignore"),
        ("Makefile.jinja", "Makefile"),
        ("kandra.yaml.jinja", "kandra.yaml"),
        ("src/pkg/__init__.py.jinja", f"src/{pkg}/__init__.py"),
        ("src/pkg/handlers/__init__.py.jinja", f"src/{pkg}/handlers/__init__.py"),
        ("src/pkg/handlers/ping.py.jinja", f"src/{pkg}/handlers/ping.py"),
        ("src/pkg/codecs/__init__.py.jinja", f"src/{pkg}/codecs/__init__.py"),
        ("src/pkg/codecs/json_codec.py.jinja", f"src/{pkg}/codecs/json_codec.py"),
        ("src/pkg/transports/__init__.py.jinja", f"src/{pkg}/transports/__init__.py"),
        ("tests/test_smoke.py.jinja", "tests/test_smoke.py"),
        ("examples/connect.py.jinja", "examples/connect.py"),
    ]
    for t in answers.transports:
        plan.append(
            (
                f"src/pkg/transports/{t.family}.py.jinja",
                f"src/{pkg}/transports/{t.family}.py",
            )
        )
    return plan


def _build_context(answers: Answers) -> dict[str, object]:
    """Build the Jinja render context.

    Pre-computes derived values (handler dotted paths, transport YAML
    fragments, etc.) so templates stay declarative.
    """
    pkg = answers.package_name
    transports_ctx = []
    for t in answers.transports:
        adapter_path = f"{pkg}.transports.{t.family}:{t.family.capitalize()}Adapter"
        if t.wire_format == "json":
            codec_path = f"{pkg}.codecs.json_codec:JsonCodec"
        elif t.wire_format == "protobuf":
            codec_path = f"{pkg}.codecs.json_codec:JsonCodec"  # stub; user replaces
        else:
            codec_path = f"{pkg}.codecs.json_codec:JsonCodec"
        transports_ctx.append(
            {
                "id": t.family,
                "family": t.family,
                "wire_format": t.wire_format,
                "adapter": adapter_path,
                "codec": codec_path,
                "service_uuid": t.service_uuid,
                "name_prefix": t.name_prefix,
                "base_url": t.base_url,
            }
        )

    # Per-transport command wire blocks for the single stub command.
    command_http = [t for t in transports_ctx if t["family"] == "http"]
    command_ble = [t for t in transports_ctx if t["family"] == "ble"]

    kandra_dep, kandra_runtime_dep = _kandra_dependency_lines(answers.dependency_source)

    return {
        "answers": answers,
        "project_name": answers.project_name,
        "slug": answers.slug,
        "package_name": pkg,
        "device_id": answers.device_id,
        "display_name": answers.display_name,
        "audience": answers.audience,
        "python_version": answers.python_version,
        "transports": transports_ctx,
        "transport_ids": [t["id"] for t in transports_ctx],
        "command_http": command_http,
        "command_ble": command_ble,
        "handler_dotted": f"{pkg}.handlers.ping:Ping",
        "kandra_dep": kandra_dep,
        "kandra_runtime_dep": kandra_runtime_dep,
    }


def _kandra_dependency_lines(source: str) -> tuple[str, str]:
    """Build the ``[tool.poetry.dependencies]`` lines for kandra + runtime.

    kandra and kandra-runtime are not yet published to PyPI. The user chooses
    how the generated project should depend on them:

    * ``"local"`` — point at the on-disk packages via absolute ``path``
      dependencies so ``poetry install`` works immediately from this checkout.
    * ``"pypi"`` — emit ordinary version constraints for a future published
      release (won't resolve until the packages are actually on an index).

    ``"local"`` silently falls back to a version constraint when no sibling
    source tree exists (e.g. kandra was installed from a wheel).
    """
    if source == "pypi":
        return ('"*"', '"*"')
    packages_dir = Path(kandra.__file__).resolve().parents[3]
    kandra_pkg = packages_dir / "kandra"
    runtime_pkg = packages_dir / "kandra-runtime"
    if (kandra_pkg / "pyproject.toml").is_file() and (runtime_pkg / "pyproject.toml").is_file():
        return (
            f'{{ path = "{kandra_pkg.as_posix()}", develop = true }}',
            f'{{ path = "{runtime_pkg.as_posix()}", develop = true }}',
        )
    # Local requested but no sibling source tree (installed from a wheel) —
    # fall back to a version constraint and let the user point at a real index.
    return ('"*"', '"*"')
