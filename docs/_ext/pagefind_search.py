"""Instant search for the docs site, powered by Pagefind (https://pagefind.app).

Marks each page's content for indexing, then runs the Pagefind CLI over the finished HTML build, so every build --
including each ``make serve-docs`` rebuild -- ships a fresh index. The search button lives in
``_templates/sidebar/search.html``; its colors in ``_static/pagefind_theme.css``.
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from sphinx.util import logging

if TYPE_CHECKING:
    from docutils import nodes
    from sphinx.application import Sphinx

logger = logging.getLogger(__name__)

# Sphinx puts each heading's id on its <section>, so Pagefind needs the section titled to link results to it.
_SECTION_RE = re.compile(r'<section id="([^"]+)">(\s*<h[1-6]>)(.*?)(?=<a class="headerlink")', re.DOTALL)
# API entries (autodoc) get a result of their own, titled with the object's dotted name.
_API_ENTRY_RE = re.compile(r'(<dt class="sig sig-object [^"]*" id="([^"]+)")')
_TAG_RE = re.compile(r"<[^>]+>")
# Diagram source and the ¶ permalinks are noise in search excerpts.
_EXCLUDE_SELECTORS = ".mermaid, .headerlink"


def _title_section(match: re.Match[str]) -> str:
    title = html.unescape(_TAG_RE.sub("", match[3])).strip()
    return f'<section id="{match[1]}" data-pagefind-title="{html.escape(title)}">{match[2]}{match[3]}'


def _mark_for_indexing(
    app: Sphinx, pagename: str, templatename: str, context: dict[str, Any], doctree: nodes.document | None
) -> None:
    # Only pages built from documents; the search page and the general/module indexes stay out of the index.
    if doctree is None or "body" not in context:
        return
    body = _SECTION_RE.sub(_title_section, context["body"])
    body = _API_ENTRY_RE.sub(r'\1 data-pagefind-title="\2"', body)
    context["body"] = f"<div data-pagefind-body>{body}</div>"


def _build_index(app: Sphinx, exception: Exception | None) -> None:
    if exception is not None or app.builder.format != "html":
        return
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pagefind",
            "--site",
            str(app.outdir),
            "--exclude-selectors",
            _EXCLUDE_SELECTORS,
            "--silent",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "pagefind failed, so the site has no search index (install the docs dependencies with `make install`):\n%s",
            (result.stderr or result.stdout).strip(),
        )


def setup(app: Sphinx) -> dict[str, Any]:
    """Register the indexing hooks and the search theme."""
    app.connect("html-page-context", _mark_for_indexing)
    app.connect("build-finished", _build_index)
    app.add_css_file("pagefind_theme.css")
    return {"parallel_read_safe": True, "parallel_write_safe": True}
