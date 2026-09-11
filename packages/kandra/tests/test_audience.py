"""Unit tests for :mod:`kandra.audience` — profiles, headers, effective audience."""

from __future__ import annotations

from pathlib import Path

import pytest
from kandra.audience import (
    DEFAULT_AUDIENCE,
    AudienceError,
    audience_intersects,
    load_audience_profiles,
    parse_audience_header,
    posix_relpath,
    resolve_file_audience,
)

_VALID_YAML = """
profiles:
  internal:
    description: "Full build."
    include_audience: [internal, partner_woodland, public]
    deny_substrings: []
  partner_woodland:
    description: "Partner release."
    include_audience: [partner_woodland, public]
    deny_substrings:
      - skunkworks
      - "@example.internal"
files:
  src/common/codecs/json.py: [public, partner_woodland, internal]
  src/devices/foo/handlers/logs.py: [partner_woodland, internal]
"""


# ---------------------------------------------------------------------------
# load_audience_profiles
# ---------------------------------------------------------------------------


def test_load_valid_profiles_from_string() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    assert set(profiles.profiles) == {"internal", "partner_woodland"}
    assert profiles.profiles["partner_woodland"].deny_substrings == ["skunkworks", "@example.internal"]
    assert profiles.files["src/common/codecs/json.py"] == ["public", "partner_woodland", "internal"]


def test_load_valid_profiles_from_path(tmp_path: Path) -> None:
    path = tmp_path / "audience_profiles.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    profiles = load_audience_profiles(path)
    assert "internal" in profiles.profiles


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AudienceError, match="cannot read audience profiles"):
        load_audience_profiles(tmp_path / "does_not_exist.yaml")


def test_load_empty_document_raises() -> None:
    with pytest.raises(AudienceError, match="is empty"):
        load_audience_profiles("")


def test_load_non_mapping_raises() -> None:
    with pytest.raises(AudienceError, match="must be a mapping"):
        load_audience_profiles("- just\n- a\n- list\n")


def test_load_invalid_yaml_raises() -> None:
    with pytest.raises(AudienceError, match="YAML parse error"):
        load_audience_profiles("profiles: [unclosed\n")


def test_load_no_profiles_raises() -> None:
    with pytest.raises(AudienceError, match="invalid audience profiles"):
        load_audience_profiles("files: {}\n")


def test_load_rejects_glob_in_files_path() -> None:
    yaml = """
profiles:
  internal:
    include_audience: [internal]
files:
  "src/devices/foo/*.py": [internal]
"""
    with pytest.raises(AudienceError, match="glob metacharacter"):
        load_audience_profiles(yaml)


def test_load_rejects_empty_audience_list() -> None:
    yaml = """
profiles:
  internal:
    include_audience: [internal]
files:
  src/foo.py: []
"""
    with pytest.raises(AudienceError, match="empty audience list"):
        load_audience_profiles(yaml)


def test_load_rejects_unknown_field() -> None:
    yaml = """
profiles:
  internal:
    include_audience: [internal]
    bogus: true
"""
    with pytest.raises(AudienceError, match="invalid audience profiles"):
        load_audience_profiles(yaml)


# ---------------------------------------------------------------------------
# AudienceProfiles helpers
# ---------------------------------------------------------------------------


def test_profile_lookup_success() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    assert profiles.profile("internal").include_audience == ["internal", "partner_woodland", "public"]


def test_profile_lookup_unknown_raises() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    with pytest.raises(AudienceError, match="unknown profile 'nope'"):
        profiles.profile("nope")


def test_grant_for_listed_file() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    assert profiles.grant_for("src/devices/foo/handlers/logs.py") == frozenset({"partner_woodland", "internal"})


def test_grant_for_unlisted_file_defaults_internal() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    assert profiles.grant_for("src/devices/foo/handlers/unknown.py") == frozenset(DEFAULT_AUDIENCE)


# ---------------------------------------------------------------------------
# parse_audience_header
# ---------------------------------------------------------------------------


def test_parse_header_absent() -> None:
    assert parse_audience_header("x = 1\n") is None


def test_parse_header_single_tag() -> None:
    assert parse_audience_header("# kandra-audience: internal\n") == ["internal"]


def test_parse_header_multiple_tags_comma_and_space() -> None:
    assert parse_audience_header("# kandra-audience: internal, partner_woodland  public\n") == [
        "internal",
        "partner_woodland",
        "public",
    ]


def test_parse_header_with_leading_indent() -> None:
    assert parse_audience_header("    #   kandra-audience:   public\n") == ["public"]


def test_parse_header_first_comment_wins() -> None:
    source = "# kandra-audience: internal\n# kandra-audience: public\n"
    assert parse_audience_header(source) == ["internal"]


def test_parse_header_ignores_token_inside_string() -> None:
    # The token appears only inside a string literal, not a comment line.
    source = 'MESSAGE = "kandra-audience: public"\n'
    assert parse_audience_header(source) is None


# ---------------------------------------------------------------------------
# resolve_file_audience
# ---------------------------------------------------------------------------


def test_resolve_unlisted_no_header_is_internal() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    assert resolve_file_audience("src/new.py", "x = 1\n", profiles) == frozenset({"internal"})


def test_resolve_listed_no_header_uses_grant() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    effective = resolve_file_audience("src/common/codecs/json.py", "x = 1\n", profiles)
    assert effective == frozenset({"public", "partner_woodland", "internal"})


def test_resolve_header_narrows_grant() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    source = "# kandra-audience: internal\nx = 1\n"
    effective = resolve_file_audience("src/common/codecs/json.py", source, profiles)
    assert effective == frozenset({"internal"})


def test_resolve_header_cannot_widen_grant() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    source = "# kandra-audience: public\n"
    with pytest.raises(AudienceError, match="narrow but never widen"):
        resolve_file_audience("src/devices/foo/handlers/logs.py", source, profiles)


def test_resolve_default_grant_header_widen_errors() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    source = "# kandra-audience: public\n"
    with pytest.raises(AudienceError, match="narrow but never widen"):
        resolve_file_audience("src/unlisted.py", source, profiles)


def test_resolve_malformed_header_tag_errors() -> None:
    profiles = load_audience_profiles(_VALID_YAML)
    source = "# kandra-audience: Not_A_Tag!\n"
    with pytest.raises(AudienceError, match="malformed audience tag"):
        resolve_file_audience("src/common/codecs/json.py", source, profiles)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def test_audience_intersects() -> None:
    assert audience_intersects(["a", "b"], ["b", "c"]) is True
    assert audience_intersects(["a"], ["b", "c"]) is False


def test_posix_relpath(tmp_path: Path) -> None:
    base = tmp_path
    target = tmp_path / "src" / "devices" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    assert posix_relpath(target, base) == "src/devices/foo.py"
