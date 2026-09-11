"""Leakage scan: fail the build if a generated SDK contains forbidden substrings.

The final IP-isolation gate. After vendoring + formatting, the generated tree is
scanned for every ``deny_substrings`` entry declared by the active audience
profile (internal codenames, author emails, source-repo paths, partner names,
…). Any hit fails the build — a partner artifact must not contain a single
forbidden token, including inside user-visible metadata like
``_generated_from.json`` or a generated ``CHANGELOG.md``.

Matching is a plain case-sensitive substring test, mirroring a ``grep``. Binary
files (and ``__pycache__``) are skipped: the denylist targets human-readable
leakage, and byte artifacts cannot be meaningfully audited this way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class LeakageError(Exception):
    """Raised when the generated tree contains one or more forbidden substrings."""


@dataclass(frozen=True)
class LeakageHit:
    """One forbidden-substring match found during the scan.

    Attributes:
        path: The file the substring was found in.
        line_number: 1-based line number of the match.
        substring: The forbidden substring that matched.
        line: The trimmed offending line (for the error report).
    """

    path: Path
    line_number: int
    substring: str
    line: str


def scan_tree(root: Path, deny_substrings: Sequence[str]) -> list[LeakageHit]:
    """Scan every text file under ``root`` for any forbidden substring.

    Args:
        root: Directory to scan recursively.
        deny_substrings: Substrings that must not appear anywhere in the tree.

    Returns:
        Every match found, ordered by path then line number. Empty when clean.
    """
    denylist = [s for s in deny_substrings if s]
    if not denylist:
        return []

    hits: list[LeakageHit] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary asset or unreadable file — not auditable as text
        for line_number, line in enumerate(text.splitlines(), start=1):
            for substring in denylist:
                if substring in line:
                    hits.append(
                        LeakageHit(
                            path=path,
                            line_number=line_number,
                            substring=substring,
                            line=line.strip(),
                        )
                    )
    return hits


def assert_no_leakage(root: Path, deny_substrings: Sequence[str], *, base: Path | None = None) -> None:
    """Raise :class:`LeakageError` if any forbidden substring appears under ``root``.

    Args:
        root: Directory to scan recursively.
        deny_substrings: Substrings that must not appear anywhere in the tree.
        base: Optional directory to render hit paths relative to (for tidy
            error messages). Defaults to ``root``.

    Raises:
        LeakageError: One or more forbidden substrings were found.
    """
    hits = scan_tree(root, deny_substrings)
    if not hits:
        return
    anchor = base if base is not None else root
    raise LeakageError(_format_hits(hits, anchor))


def _format_hits(hits: Sequence[LeakageHit], anchor: Path) -> str:
    """Build a human-readable multi-line report of leakage hits."""
    lines = [f"leakage scan failed: {len(hits)} forbidden substring match(es):"]
    for hit in hits:
        try:
            shown = hit.path.relative_to(anchor)
        except ValueError:
            shown = hit.path
        lines.append(f"  - {shown}:{hit.line_number}: {hit.substring!r} in {hit.line!r}")
    return "\n".join(lines)
