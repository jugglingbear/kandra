"""Unit tests for the scaffold slug/identifier derivation helpers."""

from __future__ import annotations

import types

import pytest
from kandra.scaffold import Answers, TransportAnswer, render, renderer
from kandra.scaffold.answers import _pythonize, _slugify

_T = TransportAnswer(family="http", wire_format="json", base_url="http://192.168.1.1:8080")


def test_slugify_empty_input_falls_back_to_default() -> None:
    assert _slugify("###") == "device-sdk"


def test_slugify_prefixes_leading_non_alpha() -> None:
    assert _slugify("123 cam").startswith("x-")


def test_slugify_normalizes_separators_and_case() -> None:
    assert _slugify("My Cool Device!!") == "my-cool-device"


def test_pythonize_converts_hyphens() -> None:
    assert _pythonize("my-cool-device") == "my_cool_device"


def test_pythonize_rejects_invalid_identifier() -> None:
    with pytest.raises(ValueError, match="valid Python identifier"):
        _pythonize("123bad")


def test_answers_rejects_invalid_slug() -> None:
    with pytest.raises(ValueError, match="invalid slug"):
        Answers.from_partial(
            project_name="Dev",
            transports=[_T],
            slug="Bad Slug!",
            package_name="valid_pkg",
            device_id="valid_dev",
        )


def test_answers_rejects_invalid_package_name() -> None:
    with pytest.raises(ValueError, match="invalid Python identifier"):
        Answers.from_partial(project_name="Dev", transports=[_T], package_name="Bad Name!")


def test_answers_rejects_invalid_audience_tag() -> None:
    with pytest.raises(ValueError, match="invalid audience tag"):
        Answers.from_partial(project_name="Dev", transports=[_T], audience=["Bad Tag!"])


def test_render_with_protobuf_raw_codecs_and_pypi_deps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # protobuf + raw wire formats exercise the non-json codec branches; pypi
    # dependency_source exercises the version-constraint path.
    answers = Answers.from_partial(
        project_name="Multi Codec Device",
        transports=[
            TransportAnswer(family="ble", wire_format="protobuf", service_uuid="abcdef01-2345-6789-abcd-ef0123456789"),
            TransportAnswer(family="http", wire_format="raw", base_url="http://192.168.1.1:8080"),
        ],
        dependency_source="pypi",
    )
    result = render(answers, tmp_path / "dev")
    assert result.files
    pyproject = (tmp_path / "dev" / "pyproject.toml").read_text(encoding="utf-8")
    assert '"*"' in pyproject  # pypi -> version constraint, not a path dep


def test_kandra_dependency_lines_local_fallback(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    # "local" with no sibling source tree (e.g. installed from a wheel) falls
    # back to a version constraint instead of a path dependency.
    fake_init = tmp_path / "a" / "b" / "c" / "d" / "kandra" / "__init__.py"
    monkeypatch.setattr(renderer, "kandra", types.SimpleNamespace(__file__=str(fake_init)))
    assert renderer._kandra_dependency_lines("local") == ('"*"', '"*"')
