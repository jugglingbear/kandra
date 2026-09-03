"""CLI entry point for the Kandra generator.

Subcommands:

* ``kandra schema``      — dump the manifest JSON Schema (for editor
                          autocomplete / external validation).
* ``kandra validate``    — load and validate a manifest YAML.
* ``kandra build``       — generate the SDK package for a manifest.
* ``kandra create-sdk``  — scaffold a new Poetry project that uses kandra
                          (interactive wizard or YAML-driven).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kandra.generator import BuildError, build_sdk
from kandra.loader import LoaderError, load_manifest
from kandra.manifest import Manifest
from kandra.scaffold import (
    Answers,
    ScaffoldError,
    ensure_target_available,
    load_answers,
    render,
    run_wizard,
)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH[args.command]
    return handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kandra", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema = subparsers.add_parser("schema", help="emit the manifest JSON Schema")
    schema.add_argument("--indent", type=int, default=2, help="JSON indent (default: 2)")

    validate = subparsers.add_parser("validate", help="validate a manifest YAML file")
    validate.add_argument("path", type=Path, help="path to the manifest YAML")

    build = subparsers.add_parser("build", help="generate the SDK package for a manifest")
    build.add_argument("path", type=Path, help="path to the manifest YAML")
    build.add_argument(
        "--output-dir",
        "--out",
        dest="output_dir",
        type=Path,
        default=None,
        help="output root directory (default: <manifest_dir>/dist)",
    )
    build.add_argument(
        "--clean",
        action="store_true",
        help="remove the target SDK package directory before writing (defaults to off)",
    )
    build.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="skip the post-generation compile+import safety check (not recommended)",
    )
    build.add_argument(
        "--typecheck",
        action="store_true",
        help="also run mypy --strict over the generated package (requires mypy)",
    )

    create = subparsers.add_parser(
        "create-sdk",
        help="scaffold a new Poetry project that uses kandra",
    )
    create.add_argument(
        "path",
        type=Path,
        help="target directory to create (must not already contain files)",
    )
    create.add_argument(
        "--non-interactive",
        action="store_true",
        help="skip the wizard; requires --answers",
    )
    create.add_argument(
        "--answers",
        type=Path,
        default=None,
        help="YAML file with wizard answers (required with --non-interactive)",
    )

    return parser


def _cmd_schema(args: argparse.Namespace) -> int:
    schema = Manifest.model_json_schema()
    json.dump(schema, sys.stdout, indent=args.indent, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(args.path)
    except LoaderError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"OK: {args.path} \u2014 device={manifest.device.id} "
        f"transports={len(manifest.transports)} commands={len(manifest.commands)}"
    )
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        result = build_sdk(
            args.path,
            output_root=args.output_dir,
            clean=args.clean,
            verify=args.verify,
            typecheck=args.typecheck,
        )
    except (LoaderError, BuildError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Wrote {result.package_name} \u2192 {result.package_path}")
    for f in result.files:
        print(f"  {f.relative_to(result.package_path.parent)}")
    return 0


def _cmd_create_sdk(args: argparse.Namespace) -> int:
    """Scaffold a new Poetry project — interactive wizard or YAML-driven."""
    if args.non_interactive and args.answers is None:
        print("create-sdk: --non-interactive requires --answers FILE.yaml", file=sys.stderr)
        return 2
    # Fail fast before the (possibly interactive) wizard: scaffolding is a
    # one-shot generator that won't overwrite an existing project, so check
    # the target up front rather than asking a pile of questions first.
    try:
        ensure_target_available(args.path)
    except ScaffoldError as exc:
        print(f"create-sdk: {exc}", file=sys.stderr)
        return 1
    try:
        answers: Answers = (
            load_answers(args.answers) if args.answers is not None else run_wizard(args.path)
        )
    except KeyboardInterrupt:
        print("\ncreate-sdk: cancelled.", file=sys.stderr)
        return 130
    except (ScaffoldError, ValueError) as exc:
        print(f"create-sdk: {exc}", file=sys.stderr)
        return 1

    try:
        result = render(answers, args.path)
    except ScaffoldError as exc:
        print(f"create-sdk: {exc}", file=sys.stderr)
        return 1

    print(f"Scaffolded {answers.project_name} \u2192 {result.target}")
    print(f"  wrote {len(result.files)} files")
    print("\nNext steps:")
    print(f"  cd {args.path}")
    print("  poetry install")
    print("  poetry run kandra validate kandra.yaml")
    print("  poetry run kandra build kandra.yaml")
    return 0


_DISPATCH = {
    "schema": _cmd_schema,
    "validate": _cmd_validate,
    "build": _cmd_build,
    "create-sdk": _cmd_create_sdk,
}


if __name__ == "__main__":
    raise SystemExit(main())
