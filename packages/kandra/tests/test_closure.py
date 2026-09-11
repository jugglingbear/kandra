"""Unit tests for :mod:`kandra.closure` — the import-closure walker."""

from __future__ import annotations

from pathlib import Path

import pytest
from kandra.closure import ClosureError, build_module_index, walk_closure


def _build_tree(root: Path) -> Path:
    """Create a small source tree under ``root/src`` and return the src dir.

    Layout::

        common/__init__.py
        common/util.py
        common/codecs/__init__.py
        common/codecs/json.py      -> imports common.util
        devices/__init__.py
        devices/foo/__init__.py
        devices/foo/handler.py     -> imports common.codecs.json, os, .sibling
        devices/foo/sibling.py
        devices/foo/dynamic.py     -> not statically imported
        devices/foo/secret.py      -> imported by handler, but exclude-able
        devices/foo/assets/data.json
    """
    src = root / "src"
    files = {
        "common/__init__.py": "",
        "common/util.py": "VALUE = 1\n",
        "common/codecs/__init__.py": "",
        "common/codecs/json.py": "from __future__ import annotations\n\nfrom common.util import VALUE\n",
        "devices/__init__.py": "",
        "devices/foo/__init__.py": "",
        "devices/foo/handler.py": (
            "from __future__ import annotations\n\n"
            "import os\n"
            "from common.codecs.json import VALUE\n"
            "from devices.foo import secret\n"
            "from . import sibling\n\n"
            "_ = (os, VALUE, secret, sibling)\n"
        ),
        "devices/foo/sibling.py": "SIBLING = 2\n",
        "devices/foo/dynamic.py": "CALIB = 3\n",
        "devices/foo/secret.py": "SECRET = 4\n",
        "devices/foo/assets/data.json": '{"k": 1}\n',
    }
    for rel, text in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src


def _dotted(src: Path, result_paths: set[Path]) -> set[str]:
    return {p.relative_to(src).as_posix() for p in result_paths}


def test_walk_includes_transitive_imports_and_ancestors(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(["devices.foo.handler"], [src])
    got = _dotted(src, set(result.all_files))
    assert got == {
        "common/__init__.py",
        "common/util.py",
        "common/codecs/__init__.py",
        "common/codecs/json.py",
        "devices/__init__.py",
        "devices/foo/__init__.py",
        "devices/foo/handler.py",
        "devices/foo/sibling.py",
        "devices/foo/secret.py",
    }


def test_walk_skips_stdlib_and_external(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(["devices.foo.handler"], [src])
    # `os` is stdlib -> never vendored.
    assert all("os.py" not in p.as_posix() for p in result.all_files)


def test_top_level_packages(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(["devices.foo.handler"], [src])
    assert result.top_level_packages == frozenset({"common", "devices"})


def test_entry_point_outside_roots_is_skipped(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(["kandra_runtime.http", "devices.foo.sibling"], [src])
    got = _dotted(src, set(result.all_files))
    # Only sibling (+ its ancestors) — the external entry contributes nothing.
    assert "devices/foo/sibling.py" in got
    assert not any("handler" in g for g in got)


def test_exclude_drops_reachable_file(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(
        ["devices.foo.handler"],
        [src],
        exclude=["devices/foo/secret.py"],
    )
    got = _dotted(src, set(result.all_files))
    assert "devices/foo/secret.py" not in got
    assert "devices/foo/handler.py" in got


def test_extra_include_python_module_is_added_and_walked(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    # dynamic.py is not statically imported anywhere; force it in.
    result = walk_closure(
        ["devices.foo.sibling"],
        [src],
        extra_include=["devices/foo/dynamic.py"],
    )
    got = _dotted(src, set(result.all_files))
    assert "devices/foo/dynamic.py" in got


def test_extra_include_directory_adds_assets(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(
        ["devices.foo.handler"],
        [src],
        extra_include=["devices/foo/assets/"],
    )
    assets = {p.relative_to(src).as_posix() for p in result.assets}
    assert assets == {"devices/foo/assets/data.json"}


def test_extra_include_glob(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(
        ["devices.foo.handler"],
        [src],
        extra_include=["devices/foo/assets/*.json"],
    )
    assert any(p.name == "data.json" for p in result.assets)


def test_extra_include_no_match_raises(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    with pytest.raises(ClosureError, match="matched no files"):
        walk_closure(["devices.foo.handler"], [src], extra_include=["devices/foo/nope.py"])


def test_relative_import_resolves(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    result = walk_closure(["devices.foo.handler"], [src])
    got = _dotted(src, set(result.all_files))
    # `from . import sibling` must be followed.
    assert "devices/foo/sibling.py" in got


def test_syntax_error_in_walked_file_raises(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    (src / "devices" / "foo" / "handler.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(ClosureError, match="cannot parse"):
        walk_closure(["devices.foo.handler"], [src])


def test_build_module_index_collision_raises(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    for root in (root_a, root_b):
        pkg = root / "shared"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(ClosureError, match="defined by two source roots"):
        build_module_index([root_a, root_b])


def test_build_module_index_maps_packages_and_modules(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    index = build_module_index([src])
    assert index["common"].is_package is True
    assert index["common.util"].is_package is False
    assert index["common.codecs.json"].path == src / "common" / "codecs" / "json.py"


def test_build_module_index_skips_root_init(tmp_path: Path) -> None:
    # An ``__init__.py`` at the source-root top has an empty dotted name and is skipped.
    root = tmp_path / "src"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "mod.py").write_text("X = 1\n", encoding="utf-8")
    index = build_module_index([root])
    assert "" not in index
    assert "mod" in index


def test_over_climbing_relative_import_is_skipped(tmp_path: Path) -> None:
    # `from ... import x` in a shallow module climbs above the top level -> ignored,
    # not crashed. The module is still vendored; the bad relative import yields nothing.
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("from ... import nothing  # noqa\n", encoding="utf-8")
    result = walk_closure(["pkg.mod"], [src])
    got = {p.relative_to(src).as_posix() for p in result.all_files}
    assert "pkg/mod.py" in got
