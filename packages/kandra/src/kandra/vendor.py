"""Vendor the import closure into the generated package and rewrite its imports.

The closure walker (:mod:`kandra.closure`) decides *which* files ship; this
module *copies* them into the SDK and rewrites their imports so the result is
self-contained.

Layout: every vendored file keeps its path relative to its source root but is
re-homed under ``<package>/_internal/``. A module authored as
``common.codecs.json`` (source root ``src``) lands at
``<package>/_internal/common/codecs/json.py`` and is importable as
``<package>._internal.common.codecs.json``.

Import rewriting is **textual, not AST-round-tripped**: only the module
reference inside each absolute ``import`` / ``from`` statement is edited, so
comments (including ``# kandra-audience:`` headers), formatting, and docstrings
survive verbatim. Relative imports (``from . import x``) are left untouched —
the package tree is preserved under ``_internal``, so they still resolve.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from kandra.closure import ClosureResult

# The vendored subpackage that holds all copied authoring code.
INTERNAL_PKG = "_internal"

_INTERNAL_INIT_DOC = '"""Vendored authoring code. DO NOT EDIT — regenerate with `kandra build`."""\n'


class VendorError(Exception):
    """Raised when a source file cannot be vendored or its imports rewritten."""


def internal_prefix(package_name: str) -> str:
    """Return the dotted prefix vendored modules live under.

    Args:
        package_name: The generated package name (e.g. ``foo_sdk``).

    Returns:
        The prefix, e.g. ``foo_sdk._internal``.
    """
    return f"{package_name}.{INTERNAL_PKG}"


def rewrite_dotted(dotted: str, vendored_tops: frozenset[str], prefix: str) -> str:
    """Re-home a dotted module name under ``prefix`` if it names vendored code.

    Args:
        dotted: The original absolute dotted module name.
        vendored_tops: Top-level package names that were vendored.
        prefix: The internal prefix (see :func:`internal_prefix`).

    Returns:
        ``prefix.dotted`` when ``dotted``'s top-level segment is vendored,
        otherwise ``dotted`` unchanged.
    """
    top = dotted.split(".", 1)[0]
    return f"{prefix}.{dotted}" if top in vendored_tops else dotted


def vendor_closure(
    closure: ClosureResult,
    roots: Sequence[Path],
    package_path: Path,
    package_name: str,
) -> list[Path]:
    """Copy every closure file under ``package_path/_internal`` with imports rewritten.

    Args:
        closure: The computed import closure.
        roots: Source-root directories (used to compute each file's relative path).
        package_path: The generated package directory.
        package_name: The generated package name (for the internal prefix).

    Returns:
        The list of files written, in a stable order.

    Raises:
        VendorError: A source file lies outside every root, or an import
            statement cannot be safely rewritten.
    """
    resolved_roots = [r.resolve() for r in roots]
    internal_dir = package_path / INTERNAL_PKG
    prefix = internal_prefix(package_name)
    vendored_tops = closure.top_level_packages

    written: list[Path] = []
    internal_dir.mkdir(parents=True, exist_ok=True)
    init_file = internal_dir / "__init__.py"
    init_file.write_text(_INTERNAL_INIT_DOC, encoding="utf-8")
    written.append(init_file)

    for module in closure.modules:
        rel = _relative_to_roots(module.path, resolved_roots)
        dest = internal_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        source = module.path.read_text(encoding="utf-8")
        dest.write_text(rewrite_imports(source, vendored_tops, prefix), encoding="utf-8")
        written.append(dest)

    for asset in closure.assets:
        rel = _relative_to_roots(asset, resolved_roots)
        dest = internal_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, dest)
        written.append(dest)

    return written


def rewrite_imports(source: str, vendored_tops: frozenset[str], prefix: str) -> str:
    """Rewrite absolute imports of vendored packages to the ``prefix`` namespace.

    Only the module reference is edited; the rest of each statement (aliases,
    imported names) and the surrounding file are preserved byte-for-byte.
    Relative imports and imports of external packages are left unchanged.

    Args:
        source: The original module source text.
        vendored_tops: Top-level package names that were vendored.
        prefix: The internal prefix to prepend.

    Returns:
        The rewritten source text.

    Raises:
        VendorError: An import shares its line with other code (a compound
            statement), which cannot be rewritten without risking corruption.
    """
    if not vendored_tops:
        return source

    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, int, int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            rendered = _render_import(node, vendored_tops, prefix)
        elif isinstance(node, ast.ImportFrom):
            rendered = _render_import_from(node, vendored_tops, prefix)
        else:
            continue
        if rendered is None:
            continue

        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno is not None else node.lineno
        end_col = node.end_col_offset if node.end_col_offset is not None else len(lines[end - 1])
        indent = lines[start][: node.col_offset]
        if indent.strip():
            raise VendorError(
                f"cannot rewrite import on line {node.lineno}: it shares the line with other "
                "code (split compound statements onto their own lines to vendor this module)"
            )
        edits.append((start, end, end_col, indent + rendered))

    for start, end, end_col, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        last_line = lines[end - 1]
        trailing = _trailing_comment(last_line, end_col)
        lines[start:end] = [replacement + trailing + _trailing_newline(last_line)]
    return "".join(lines)


# ---------------------------------------------------------------------------
# Statement rendering
# ---------------------------------------------------------------------------


def _render_import(node: ast.Import, vendored_tops: frozenset[str], prefix: str) -> str | None:
    """Render an ``import a, b as c`` statement, or ``None`` if nothing changed."""
    changed = False
    parts: list[str] = []
    for alias in node.names:
        new_name = rewrite_dotted(alias.name, vendored_tops, prefix)
        changed = changed or new_name != alias.name
        parts.append(f"{new_name} as {alias.asname}" if alias.asname else new_name)
    return "import " + ", ".join(parts) if changed else None


def _render_import_from(node: ast.ImportFrom, vendored_tops: frozenset[str], prefix: str) -> str | None:
    """Render a ``from mod import a, b`` statement, or ``None`` if nothing changed."""
    if node.level != 0 or node.module is None:
        return None  # relative import — the vendored tree preserves it as-is
    new_module = rewrite_dotted(node.module, vendored_tops, prefix)
    if new_module == node.module:
        return None  # external module — untouched
    names = ", ".join(f"{a.name} as {a.asname}" if a.asname else a.name for a in node.names)
    return f"from {new_module} import {names}"


# ---------------------------------------------------------------------------
# Path / text helpers
# ---------------------------------------------------------------------------


def _relative_to_roots(path: Path, roots: Sequence[Path]) -> Path:
    """Return ``path`` relative to whichever root contains it."""
    resolved = path.resolve()
    for root in roots:
        if resolved.is_relative_to(root):
            return resolved.relative_to(root)
    raise VendorError(f"file {path} is not under any source root {list(map(str, roots))}")


def _trailing_newline(line: str) -> str:
    """Return the newline sequence terminating ``line`` (``''`` if none)."""
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _trailing_comment(last_line: str, end_col: int) -> str:
    """Return any inline comment after an import on its final line.

    Preserves a workaround note like ``from x import y  # noqa`` when the import
    statement is rewritten. Returns ``''`` when nothing but whitespace follows.
    """
    tail = last_line[end_col:].rstrip("\r\n")
    return tail if "#" in tail else ""
