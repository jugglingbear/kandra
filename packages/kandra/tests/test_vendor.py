"""Unit tests for :mod:`kandra.vendor` — import rewriting + closure vendoring."""

from __future__ import annotations

from pathlib import Path

import pytest
from kandra.closure import ClosureResult, ModuleFile, walk_closure
from kandra.vendor import (
    VendorError,
    internal_prefix,
    rewrite_dotted,
    rewrite_imports,
    vendor_closure,
)

_TOPS = frozenset({"common", "devices"})
_PREFIX = "foo_sdk._internal"


# ---------------------------------------------------------------------------
# rewrite_dotted / internal_prefix
# ---------------------------------------------------------------------------


def test_internal_prefix() -> None:
    assert internal_prefix("foo_sdk") == "foo_sdk._internal"


def test_rewrite_dotted_vendored() -> None:
    assert rewrite_dotted("common.codecs.json", _TOPS, _PREFIX) == "foo_sdk._internal.common.codecs.json"


def test_rewrite_dotted_external_untouched() -> None:
    assert rewrite_dotted("kandra_runtime.http", _TOPS, _PREFIX) == "kandra_runtime.http"
    assert rewrite_dotted("os", _TOPS, _PREFIX) == "os"


# ---------------------------------------------------------------------------
# rewrite_imports
# ---------------------------------------------------------------------------


def test_rewrite_from_import_vendored() -> None:
    src = "from common.codecs.json import JsonCodec\n"
    assert rewrite_imports(src, _TOPS, _PREFIX) == "from foo_sdk._internal.common.codecs.json import JsonCodec\n"


def test_rewrite_plain_import_with_alias() -> None:
    src = "import devices.foo.handler as h\n"
    assert rewrite_imports(src, _TOPS, _PREFIX) == "import foo_sdk._internal.devices.foo.handler as h\n"


def test_rewrite_multiple_names() -> None:
    src = "from common.util import a, b as c\n"
    assert rewrite_imports(src, _TOPS, _PREFIX) == "from foo_sdk._internal.common.util import a, b as c\n"


def test_external_import_untouched() -> None:
    src = "from kandra_runtime import Command\nimport os\n"
    assert rewrite_imports(src, _TOPS, _PREFIX) == src


def test_relative_import_untouched() -> None:
    src = "from . import sibling\nfrom .codecs import json\n"
    assert rewrite_imports(src, _TOPS, _PREFIX) == src


def test_rewrite_preserves_comments_including_audience_header() -> None:
    src = (
        '"""Module docstring."""\n'
        "# kandra-audience: internal\n"
        "from __future__ import annotations\n\n"
        "from common.util import VALUE  # keep this comment\n"
    )
    out = rewrite_imports(src, _TOPS, _PREFIX)
    assert "# kandra-audience: internal" in out
    assert "# keep this comment" in out
    assert "from foo_sdk._internal.common.util import VALUE" in out


def test_rewrite_multiline_parenthesized_from_import() -> None:
    src = "from common.util import (\n    a,\n    b,\n)\n"
    out = rewrite_imports(src, _TOPS, _PREFIX)
    assert out == "from foo_sdk._internal.common.util import a, b\n"


def test_rewrite_nested_import_indentation_preserved() -> None:
    src = "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from common.util import VALUE\n"
    out = rewrite_imports(src, _TOPS, _PREFIX)
    assert "    from foo_sdk._internal.common.util import VALUE\n" in out


def test_rewrite_no_change_returns_identical() -> None:
    src = "x = 1\nimport os\n"
    assert rewrite_imports(src, _TOPS, _PREFIX) is src or rewrite_imports(src, _TOPS, _PREFIX) == src


def test_rewrite_empty_tops_returns_identical() -> None:
    src = "from common.util import VALUE\n"
    assert rewrite_imports(src, frozenset(), _PREFIX) == src


def test_rewrite_compound_statement_raises() -> None:
    src = "import sys; from common.util import VALUE\n"
    with pytest.raises(VendorError, match="shares the line"):
        rewrite_imports(src, _TOPS, _PREFIX)


def test_rewrite_preserves_crlf_line_endings() -> None:
    src = "from common.util import VALUE\r\n"
    assert rewrite_imports(src, _TOPS, _PREFIX) == "from foo_sdk._internal.common.util import VALUE\r\n"


def test_rewrite_import_without_trailing_newline() -> None:
    src = "from common.util import VALUE"  # no trailing newline
    assert rewrite_imports(src, _TOPS, _PREFIX) == "from foo_sdk._internal.common.util import VALUE"


# ---------------------------------------------------------------------------
# vendor_closure
# ---------------------------------------------------------------------------


def _build_tree(root: Path) -> Path:
    src = root / "src"
    files = {
        "common/__init__.py": "",
        "common/util.py": "VALUE = 1\n",
        "devices/__init__.py": "",
        "devices/foo/__init__.py": "",
        "devices/foo/handler.py": (
            "from __future__ import annotations\n\nfrom common.util import VALUE\n\n_ = VALUE\n"
        ),
        "devices/foo/assets/data.json": '{"k": 1}\n',
    }
    for rel, text in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return src


def test_vendor_closure_copies_tree_and_rewrites(tmp_path: Path) -> None:
    src = _build_tree(tmp_path)
    closure = walk_closure(
        ["devices.foo.handler"],
        [src],
        extra_include=["devices/foo/assets/"],
    )
    pkg = tmp_path / "dist" / "foo_sdk"
    written = vendor_closure(closure, [src], pkg, "foo_sdk")

    internal = pkg / "_internal"
    assert (internal / "__init__.py").is_file()
    assert (internal / "common" / "util.py").is_file()
    assert (internal / "devices" / "foo" / "handler.py").is_file()
    # Asset copied verbatim.
    assert (internal / "devices" / "foo" / "assets" / "data.json").read_text(encoding="utf-8") == '{"k": 1}\n'
    # Import rewritten inside the vendored module.
    handler_text = (internal / "devices" / "foo" / "handler.py").read_text(encoding="utf-8")
    assert "from foo_sdk._internal.common.util import VALUE" in handler_text
    # Every written path is returned.
    assert (internal / "__init__.py") in written


def test_vendor_closure_rejects_file_outside_roots(tmp_path: Path) -> None:
    stray = tmp_path / "outside" / "stray.py"
    stray.parent.mkdir(parents=True)
    stray.write_text("X = 1\n", encoding="utf-8")
    closure = ClosureResult(
        modules=(ModuleFile(dotted="stray", path=stray, is_package=False),),
        assets=(),
        imports={},
    )
    pkg = tmp_path / "dist" / "foo_sdk"
    with pytest.raises(VendorError, match="not under any source root"):
        vendor_closure(closure, [tmp_path / "src"], pkg, "foo_sdk")
