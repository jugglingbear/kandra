"""Import-closure walker: discover the source files a manifest's handlers pull in.

The generator emits thin glue (registry / client), but the *substance* of an SDK
is the user's handler / codec / transport / model modules. To ship a
self-contained package we must copy those files — and only those files — out of
the authoring tree. This module computes that set.

Starting from the manifest's entry-point modules (handler/codec/transport dotted
paths), it parses each module's AST, follows every ``import`` / ``from ... import``
that resolves *inside* the declared ``source_roots``, and repeats transitively.
Imports that resolve to the stdlib, third-party packages, or the public runtime
are treated as external and left alone.

Two manifest escape hatches are honored (see ``Vendoring`` in the manifest model):

* ``exclude`` — drop a reachable file from the closure (e.g. internal bench
  tooling that must never ship).
* ``extra_include`` — force-add a file / directory / glob the static walk cannot
  discover (dynamically imported modules, non-Python asset files).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


class ClosureError(Exception):
    """Raised when the import closure cannot be computed.

    Causes include an unparseable source file, a dotted entry point that does
    not resolve under any source root, or two source roots defining the same
    top-level module name.
    """


@dataclass(frozen=True)
class ModuleFile:
    """A Python module discovered under a source root.

    Attributes:
        dotted: The importable dotted name (e.g. ``common.codecs.json``). For a
            package this is the package name (``common.codecs``).
        path: Absolute path to the ``.py`` file on disk.
        is_package: ``True`` when ``path`` is an ``__init__.py``.
    """

    dotted: str
    path: Path
    is_package: bool


@dataclass(frozen=True)
class ClosureResult:
    """The outcome of :func:`walk_closure`.

    Attributes:
        modules: Every ``.py`` module in the transitive closure (after excludes),
            each paired with its dotted name.
        assets: Non-module files pulled in via ``extra_include`` (data files,
            e.g. calibration CSVs) that must be copied verbatim.
        imports: Direct in-closure dependency edges: each module path maps to the
            set of module paths it imports (including ancestor ``__init__``
            files). Used to report the exact chain when an audience-kept file
            depends on an audience-dropped one.
    """

    modules: tuple[ModuleFile, ...]
    assets: tuple[Path, ...]
    imports: dict[Path, frozenset[Path]]

    @property
    def all_files(self) -> frozenset[Path]:
        """Return every file to vendor (module sources plus asset files)."""
        return frozenset({m.path for m in self.modules} | set(self.assets))

    @property
    def top_level_packages(self) -> frozenset[str]:
        """Return the set of top-level package names spanned by the closure.

        These are the names an import rewriter must re-home under the vendored
        ``_internal`` namespace (e.g. ``{"devices", "common"}``).
        """
        return frozenset(m.dotted.split(".", 1)[0] for m in self.modules)


def build_module_index(roots: Sequence[Path]) -> dict[str, ModuleFile]:
    """Index every importable ``.py`` module under ``roots`` by dotted name.

    Args:
        roots: Source-root directories the walker may vendor from.

    Returns:
        A mapping of dotted module name to :class:`ModuleFile`.

    Raises:
        ClosureError: Two roots define the same dotted module name.
    """
    index: dict[str, ModuleFile] = {}
    for root in roots:
        root = root.resolve()
        for path in sorted(root.rglob("*.py")):
            rel_parts = path.relative_to(root).parts
            if path.name == "__init__.py":
                dotted = ".".join(rel_parts[:-1])
                is_package = True
            else:
                dotted = ".".join([*rel_parts[:-1], path.stem])
                is_package = False
            if not dotted:
                continue
            if dotted in index and index[dotted].path != path:
                raise ClosureError(
                    f"module name {dotted!r} is defined by two source roots: " f"{index[dotted].path} and {path}"
                )
            index[dotted] = ModuleFile(dotted=dotted, path=path, is_package=is_package)
    return index


def walk_closure(
    entry_modules: Iterable[str],
    roots: Sequence[Path],
    *,
    extra_include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> ClosureResult:
    """Compute the transitive import closure of ``entry_modules`` within ``roots``.

    Args:
        entry_modules: Dotted module names of the manifest's entry points
            (handlers, codecs, transport adapters). Entries that do not resolve
            under any source root are external and silently skipped.
        roots: Source-root directories the walker may vendor from.
        extra_include: Manifest ``vendoring.extra_include`` entries — each a
            file, directory, or glob relative to a source root. Python files are
            added as seeds (and walked); other files become assets.
        exclude: Manifest ``vendoring.exclude`` entries — files / directories /
            globs (relative to a source root) removed from the closure.

    Returns:
        The :class:`ClosureResult` describing every file to vendor.

    Raises:
        ClosureError: A source file cannot be parsed, or an entry point / extra
            include does not resolve under any source root.
    """
    resolved_roots = [r.resolve() for r in roots]
    index = build_module_index(resolved_roots)
    by_path = {m.path: m for m in index.values()}
    excluded = _expand_patterns(exclude, resolved_roots)

    seeds, assets = _collect_seeds(entry_modules, extra_include, resolved_roots, by_path)

    visited: dict[Path, ModuleFile] = {}
    edges: dict[Path, frozenset[Path]] = {}
    queue: list[ModuleFile] = [m for m in seeds if m.path not in excluded]
    while queue:
        module = queue.pop()
        if module.path in visited or module.path in excluded:
            continue
        visited[module.path] = module
        deps: set[Path] = set()
        for ancestor in _ancestor_packages(module.dotted, index):
            if ancestor.path not in excluded:
                deps.add(ancestor.path)
                if ancestor.path not in visited:
                    queue.append(ancestor)
        for target in _iter_import_targets(module, index):
            if target.path not in excluded:
                deps.add(target.path)
                if target.path not in visited:
                    queue.append(target)
        edges[module.path] = frozenset(deps)

    modules = tuple(sorted(visited.values(), key=lambda m: m.dotted))
    asset_files = tuple(sorted(p for p in assets if p not in excluded))
    return ClosureResult(modules=modules, assets=asset_files, imports=edges)


# ---------------------------------------------------------------------------
# Seed collection
# ---------------------------------------------------------------------------


def _collect_seeds(
    entry_modules: Iterable[str],
    extra_include: Sequence[str],
    roots: Sequence[Path],
    by_path: dict[Path, ModuleFile],
) -> tuple[list[ModuleFile], set[Path]]:
    """Resolve entry points + extra includes into seed modules and asset files."""
    seeds: list[ModuleFile] = []
    seen: set[Path] = set()
    index_by_dotted = {m.dotted: m for m in by_path.values()}

    for dotted in entry_modules:
        module = index_by_dotted.get(dotted)
        # Entry points outside the source roots (e.g. kandra_runtime.*) are
        # external — the generated package depends on them, never vendors them.
        if module is not None and module.path not in seen:
            seeds.append(module)
            seen.add(module.path)

    assets: set[Path] = set()
    for pattern in extra_include:
        matched = _expand_patterns([pattern], roots)
        if not matched:
            raise ClosureError(f"vendoring.extra_include entry {pattern!r} matched no files under any source root")
        for path in matched:
            module = by_path.get(path)
            if module is not None:
                if path not in seen:
                    seeds.append(module)
                    seen.add(path)
            else:
                assets.add(path)
    return seeds, assets


def _expand_patterns(patterns: Sequence[str], roots: Sequence[Path]) -> set[Path]:
    """Expand file / directory / glob patterns (relative to each root) to files."""
    matches: set[Path] = set()
    for pattern in patterns:
        for root in roots:
            candidate = (root / pattern).resolve()
            if candidate.is_dir():
                matches.update(p.resolve() for p in candidate.rglob("*") if p.is_file())
            elif any(ch in pattern for ch in "*?[]"):
                matches.update(p.resolve() for p in root.glob(pattern) if p.is_file())
            elif candidate.is_file():
                matches.add(candidate)
    return matches


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


def _iter_import_targets(module: ModuleFile, index: dict[str, ModuleFile]) -> Iterator[ModuleFile]:
    """Yield the in-closure modules imported by ``module`` (external ones skipped)."""
    try:
        tree = ast.parse(module.path.read_text(encoding="utf-8"), filename=str(module.path))
    except (SyntaxError, OSError) as exc:
        raise ClosureError(f"cannot parse {module.path}: {exc}") from exc

    for dotted in _iter_imported_names(tree, module):
        target = index.get(dotted)
        if target is not None:
            yield target


def _iter_imported_names(tree: ast.Module, module: ModuleFile) -> Iterator[str]:
    """Yield absolute dotted names referenced by import statements in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from_base(node, module)
            if base is None:
                continue
            yield base
            for alias in node.names:
                if alias.name != "*":
                    yield f"{base}.{alias.name}"


def _resolve_from_base(node: ast.ImportFrom, module: ModuleFile) -> str | None:
    """Resolve the absolute base module of a ``from ... import`` statement.

    Handles relative imports by anchoring ``node.level`` against ``module``'s
    package. Returns ``None`` if the relative import climbs above the top level.
    """
    if node.level == 0:
        return node.module

    package_parts = module.dotted.split(".") if module.is_package else module.dotted.split(".")[:-1]
    ascend = node.level - 1
    if ascend > len(package_parts):
        return None
    anchor = package_parts[: len(package_parts) - ascend] if ascend else package_parts
    tail = node.module.split(".") if node.module else []
    combined = [*anchor, *tail]
    return ".".join(combined) if combined else None


def _ancestor_packages(dotted: str, index: dict[str, ModuleFile]) -> Iterator[ModuleFile]:
    """Yield the ``__init__`` modules of every parent package of ``dotted``."""
    parts = dotted.split(".")
    for depth in range(1, len(parts)):
        ancestor = index.get(".".join(parts[:depth]))
        if ancestor is not None and ancestor.is_package:
            yield ancestor
