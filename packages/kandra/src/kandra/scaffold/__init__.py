"""SDK scaffolding wizard (``kandra create-sdk``).

The scaffold renders a minimal Poetry project that uses kandra +
kandra-runtime. The generated project's manifest passes
``kandra validate`` out of the box; commands, codecs, and adapters are
stubs that the user fills in.

Public entry points:

* :func:`run_wizard` — interactive questionary-based prompt that returns
  an :class:`Answers` instance.
* :func:`load_answers` — load an :class:`Answers` instance from a YAML
  file (for ``--non-interactive`` mode and tests).
* :func:`render` — render an :class:`Answers` to a target directory.
"""

from __future__ import annotations

from kandra.scaffold.answers import Answers, TransportAnswer
from kandra.scaffold.renderer import ScaffoldError, ensure_target_available, render
from kandra.scaffold.wizard import load_answers, run_wizard

__all__ = [
    "Answers",
    "TransportAnswer",
    "ScaffoldError",
    "ensure_target_available",
    "load_answers",
    "render",
    "run_wizard",
]
