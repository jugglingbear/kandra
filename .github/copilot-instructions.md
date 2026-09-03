# Kandra — Copilot Instructions

> **Read [`AGENTS.md`](../AGENTS.md) first.** It is the agent-agnostic source of truth for this
> repository's structure, tech stack, conventions, IP-isolation rule, and quality gate. This file only
> notes how VS Code / Copilot picks up customizations.

## Customization file layout

| Form | Location | Loaded |
|------|----------|--------|
| Agent-agnostic instructions | `AGENTS.md` (repo root) | Always |
| Copilot-specific notes | `.github/copilot-instructions.md` (this file) | Always |
| File-scoped instructions | `.github/instructions/*.instructions.md` | Auto-attached via `applyTo` glob |

The file-scoped instructions auto-attach by glob:

- `general.instructions.md` (`**`) — whitespace, EOF, line length.
- `python.instructions.md` (`**/*.py`) — ruff, mypy strict, typing, docstrings, async, testing.
- `markdown.instructions.md` (`**/*.md`) — MyST / Sphinx Markdown formatting.
- `makefile.instructions.md` (`**/Makefile,**/*.mk`) — self-documenting help, colors, standard targets.

## Before generating code

- Match the existing stack: **Poetry monorepo**, **ruff + ruff-format + mypy strict**,
  **pytest + pytest-asyncio**, **Sphinx / MyST** docs, **pydantic v2**, async-first.
- Run `make qa` before considering a change complete.
- Keep the repo **IP-isolated**: no proprietary vendor names, device codenames, or copied third-party
  code — example devices are fictional. See `AGENTS.md` → "IP Isolation".
