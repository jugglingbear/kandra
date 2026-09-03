#!/usr/bin/env python3
"""Release helpers for the kandra monorepo.

Two operations, both driven from the top-level ``Makefile``:

* ``bump_versions(part)`` -- bump both ``packages/*/pyproject.toml``
  version strings in lockstep (``patch`` / ``minor`` / ``major``).
* ``prepare_for_publish()`` / ``restore_after_publish()`` -- swap the
  ``kandra`` package's path-dep on ``kandra-runtime`` for a published
  version constraint, then restore it. ``make publish`` calls both.

Kept dependency-free (stdlib only) so it can run in any environment
including a cold CI image.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME_TOML = REPO / "packages" / "kandra-runtime" / "pyproject.toml"
KANDRA_TOML = REPO / "packages" / "kandra" / "pyproject.toml"

# The exact line we toggle when publishing. Kept in two forms so we can
# round-trip safely without a TOML parser.
DEV_DEP_LINE = 'kandra-runtime = { path = "../kandra-runtime", develop = true }'
PUB_DEP_RE = re.compile(r'^kandra-runtime\s*=\s*"\^[0-9.]+"\s*$', re.MULTILINE)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _get_version(toml: str) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.MULTILINE)
    if not m:
        raise RuntimeError("could not find version line")
    return m.group(1)


def _set_version(toml: str, new: str) -> str:
    return re.sub(
        r'^(version\s*=\s*")[^"]+("\s*)$',
        rf"\g<1>{new}\g<2>",
        toml,
        count=1,
        flags=re.MULTILINE,
    )


def _bump(version: str, part: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        raise ValueError(f"unknown version part: {part!r}")
    return f"{major}.{minor}.{patch}"


def cmd_bump(part: str) -> int:
    runtime_toml = _read(RUNTIME_TOML)
    kandra_toml = _read(KANDRA_TOML)
    old = _get_version(runtime_toml)
    if _get_version(kandra_toml) != old:
        print(
            f"refusing to bump: versions out of sync (runtime={old} "
            f"kandra={_get_version(kandra_toml)})",
            file=sys.stderr,
        )
        return 1
    new = _bump(old, part)
    _write(RUNTIME_TOML, _set_version(runtime_toml, new))
    _write(KANDRA_TOML, _set_version(kandra_toml, new))
    print(f"bumped {old} -> {new}")
    return 0


def cmd_prepare_publish() -> int:
    """Rewrite ``packages/kandra``'s runtime dep from path to version."""
    runtime_version = _get_version(_read(RUNTIME_TOML))
    # ^X.Y.Z is the published constraint that lets patches install
    # transparently while pinning the minor.
    constraint = f'kandra-runtime = "^{runtime_version}"'
    text = _read(KANDRA_TOML)
    if DEV_DEP_LINE not in text:
        if PUB_DEP_RE.search(text):
            print("already in publish form; nothing to do")
            return 0
        print("could not find the dev path-dep line; aborting", file=sys.stderr)
        return 1
    _write(KANDRA_TOML, text.replace(DEV_DEP_LINE, constraint))
    print(f"rewrote kandra-runtime dep -> {constraint}")
    return 0


def cmd_restore_dev() -> int:
    """Swap published version constraint back to the dev path-dep."""
    text = _read(KANDRA_TOML)
    if PUB_DEP_RE.search(text):
        _write(KANDRA_TOML, PUB_DEP_RE.sub(DEV_DEP_LINE, text))
        print("restored kandra-runtime dep -> path (develop)")
        return 0
    if DEV_DEP_LINE in text:
        print("already in dev form; nothing to do")
        return 0
    print("could not recognise current kandra-runtime dep form", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    bump = sub.add_parser("bump", help="bump both package versions in lockstep")
    bump.add_argument("part", choices=["major", "minor", "patch"])

    sub.add_parser("prepare-publish", help="rewrite kandra's runtime dep for publish")
    sub.add_parser("restore-dev", help="restore kandra's runtime dep to path/develop")

    args = parser.parse_args(argv)
    if args.cmd == "bump":
        return cmd_bump(args.part)
    if args.cmd == "prepare-publish":
        return cmd_prepare_publish()
    if args.cmd == "restore-dev":
        return cmd_restore_dev()
    parser.error(f"unknown cmd {args.cmd!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
