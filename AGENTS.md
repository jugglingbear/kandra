# Kandra — Agent Instructions

This file is the **agent-agnostic** source of truth for AI coding assistants working in this
repository (Copilot, Claude Code, Cursor, Aider, Codex, Continue, etc.). Tool-specific extensions
live alongside it:

- `.github/copilot-instructions.md` — GitHub Copilot / VS Code extensions
- `.github/instructions/*.instructions.md` — File-type-scoped rules auto-attached by `applyTo` glob

---

## Repository Overview

**Kandra** is a data-driven framework for producing typed, lint-clean, IP-isolated Python SDKs that
talk to embedded-Linux devices over multiple transports (BLE, HTTP/Wi-Fi, HTTP/USB-CDC, serial, …).
You feed it a device **manifest** (YAML wiring) plus hand-written handler/codec classes, and the
generator emits a self-contained SDK package.

### Monorepo layout

| Path | Purpose |
|------|---------|
| `packages/kandra-runtime/` | Pip-installable runtime (audience-agnostic): transport/codec/command protocols, dispatch, `Result[T]`. |
| `packages/kandra/` | Generator + CLI (`kandra schema/validate/build/create-sdk`): reads a manifest, vendors handlers, emits the SDK. |
| `tests/` | Cross-package end-to-end tests. |
| `examples/` | Fictional example devices (manifests + handlers), e.g. `pneumatic_bear_poker`. |
| `docs/` | Sphinx + MyST documentation site. |
| `schemas/` | Generated `manifest.schema.json` (regenerated from the pydantic models). |
| `scripts/` | Repo tooling (e.g. `release.py`). |
| `temp/` | Scratch / throwaway files (git-ignored). |

### Three-layer architecture (keep strictly separated)

1. **Authoring inputs** (never shipped): device YAML manifests, handler/codec modules, generator templates.
2. **Runtime** (`kandra-runtime`, pip-installable, audience-agnostic): protocols + dispatch + result model.
3. **Generated SDK** (per-device artifact): typed facade + vendored handlers; depends only on the runtime.

---

## IP Isolation — the cardinal rule

Kandra's headline feature is **white-label / IP-isolated SDKs**. Treat this as a hard constraint:

- **No third-party or proprietary IP in the framework or its examples.** No vendor names, device
  codenames, proprietary protocol details, internal URLs, or ticket IDs. Keep everything generic and
  self-contained.
- The generator prunes by **audience** and runs a **leakage scan** so a generated SDK never exposes
  unrelated devices, commands, or another audience's material. Don't defeat this — no cross-audience
  imports, no reaching back into the authoring repo from generated code.
- Example devices must be **fictional** (the bear-themed `pneumatic_bear_poker` is the reference).

---

## Tech Stack

- **Python** 3.11+ (`target-version = py311`), packaged with **Poetry** (one shared venv for both packages).
- **Lint / format:** `ruff` (lint + import sort) with `ruff-format`. `mypy` in **strict** mode.
- **Tests:** `pytest` + `pytest-asyncio` (`asyncio_mode = auto`); `pytest-cov`. Integration tests use
  `testcontainers` and are opt-in via `-m integration` (require Docker).
- **Docs:** Sphinx + MyST-Parser + Furo + autodoc-pydantic + `sphinxcontrib-mermaid` (Mermaid for UML).
- **Models:** pydantic v2 (frozen, `extra="forbid"`).

---

## Core Conventions

- **Async-first.** The runtime and dispatch are async; sync callers use the generated `SyncClient`
  wrapper (`asyncio.run` per call) or `dispatch_sync`. Don't add blocking I/O to async paths.
- **Code-first, not schema-first.** Python classes are the source of truth; the YAML manifest is
  *wiring only* (dotted-path references + per-transport blocks). Never put type definitions in YAML.
- **Runtime ↔ generator import boundary.** `kandra-runtime` must **never** import `kandra.*`. Enforced
  by `make check-boundary`.
- **Transports vs codecs are separate concerns.** Transport = wire envelope in/out; codec = user types
  ↔ wire envelope; a `ResponseInterpreter` maps a wire response to a `Classification`.
- **Regenerate the schema when manifest models change.** `make schema` (a pre-commit hook re-stages it).
- **120-character** line width everywhere. Google-style docstrings.
- **File hygiene:** no trailing whitespace; every file ends with exactly one newline (enforced by the
  `trailing-whitespace` / `end-of-file-fixer` pre-commit hooks).

---

## Quality Gate

Run before considering work done:

```bash
make qa      # check-boundary + lint + typecheck + check-schema + test
```

Individual targets: `make lint` (ruff), `make typecheck` (mypy strict), `make test` (pytest),
`make check-boundary`, `make check-schema`. Type `make` (or `make help`) for the full list.

- **New code must pass `make qa` with zero warnings/errors.**
- Run `make schema` after changing the manifest pydantic models.
- Don't fix unrelated pre-existing issues unless asked.

---

## Scratch / Temporary Files

Put throwaway scripts, scratch output, and intermediate artifacts in the repo-local `temp/` directory
(git-ignored). Never use `/tmp/`. Never leave scratch files in `packages/`, `tests/`, `docs/`, or the
repo root.

---

## Quick Reference

| Task | Command |
|------|---------|
| Install workspace (editable, + hooks) | `make install` |
| Full quality gate | `make qa` |
| Lint | `make lint` |
| Type-check (strict) | `make typecheck` |
| Run tests | `make test` |
| Integration tests (Docker) | `make test-integration` |
| Regenerate manifest schema | `make schema` |
| Validate example manifest | `make validate` |
| Build example SDK | `make build-examples` |
| Build docs | `make build-docs` |
| Serve docs (live reload) | `make serve-docs` |
| Run all pre-commit hooks | `make precommit` |
