---
applyTo: "**/*.py"
description: "Python coding standards: ruff, mypy strict, typing, docstrings, async, testing."
---

# Python Coding Standards

**When generating or modifying Python code**, follow these standards. New code must pass the full
quality gate (`make qa`) with zero warnings or errors.

---

## Lint & Type Checking

The enforced tools are **ruff** (lint + import sort) with **ruff-format**, and **mypy** in **strict**
mode. (A `.flake8` with the same 120 limit exists for editor parity, but ruff is the source of truth.)

```bash
make lint        # ruff check across both packages + tests + scripts
make typecheck   # mypy --strict on both packages' src
make precommit   # run ruff, ruff-format, and hygiene hooks on all files
```

- Run linters as **separate** commands; don't chain `ruff` and `mypy` with `&&`.
- Auto-fix with `poetry run ruff check <path> --fix` and `poetry run ruff format <path>`.
- **New code:** zero warnings/errors. **Existing issues:** don't fix unrelated ones unless asked.

The active ruff rule families are `E, F, I, B, UP, N, RUF, SIM, C90, ANN, D` (see `pyproject.toml`).

---

## Code Style

### Type hints

- **Required on every function, method, and attribute** — mypy strict rejects untyped defs.
- Prefer modern built-in generics (`list[str]`, `dict[str, int]`, `X | None`) over `typing.List` etc.

### Line length

- **120 characters maximum** (see `general.instructions.md` for whitespace/EOF rules).

### Docstrings

- **Google-style** docstrings (ruff pydocstyle `convention = "google"`).
- Public API docstrings are expected; `D100`/`D104` (missing module/package docstrings) and
  `ANN101`/`ANN102` (self/cls) are ignored. Tests and `scripts/` are exempt from `D`/`ANN`.
- Keep docstring indentation a multiple of 2 spaces.

### Async-first

- The runtime and dispatch are **async**. Public runtime I/O is `async def`; don't block the event loop.
- Sync callers use the generated `SyncClient` wrapper or `dispatch_sync` — don't sprinkle `asyncio.run`
  through library code.

### Models

- Use **pydantic v2** for data models — frozen (`model_config = ConfigDict(frozen=True, extra="forbid")`)
  where practical. Run `make schema` after changing the manifest models so
  `schemas/manifest.schema.json` stays in sync.

### Avoid magic values

- Don't hardcode bare numbers/strings for identifiers, opcodes, or status codes — use enums or named
  constants so intent is greppable and type-checked.

---

## Testing

- **pytest** with **pytest-asyncio** (`asyncio_mode = auto`) — write `async def test_*` directly.
- Layout mirrors the packages: runtime tests in `packages/kandra-runtime/tests/`, generator tests in
  `packages/kandra/tests/`, cross-package end-to-end tests in the root `tests/`.
- **Integration tests** (Docker via `testcontainers`) are marked `@pytest.mark.integration` and are
  opt-in: `make test-integration` (the default `make test` excludes them).
- Run the fast suite with `make test`; the full gate with `make qa`.

---

## Import Boundary

`kandra-runtime` must **never** import `kandra.*` (the generator). `make check-boundary` enforces this —
keep runtime code free of any generator dependency.

---

## Quick Checklist

Before submitting Python code:

- [ ] `make lint` (ruff) passes with no errors
- [ ] `make typecheck` (mypy strict) passes with no errors
- [ ] All functions/methods/attributes have type hints
- [ ] Google-style docstrings on public API
- [ ] No magic numbers/values (use enums/constants)
- [ ] `make test` passes; `make schema` re-run if manifest models changed
