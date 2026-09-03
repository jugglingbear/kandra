---
applyTo: "**/Makefile,**/*.mk"
description: "Makefile conventions: self-documenting help system, standard targets, ANSI colors."
---

# Makefile Standards

**When creating or modifying Makefiles**, follow these conventions for a consistent, user-friendly
experience. This repository's root `Makefile` is the reference implementation.

---

## Self-Documenting Help System

Every Makefile **must** include a `help` target that prints colorized usage. Typing `make` or
`make help` should show a scannable list of available targets.

### How it works

1. Each user-facing target's help text is an **inline `## Description`** comment on the target line.
2. The `help` target runs a single `awk` pass over `$(MAKEFILE_LIST)` and prints targets in **source order**.
3. `help` is the **default target** (`.DEFAULT_GOAL := help`, listed first).
4. `##@ Section Name` lines render as **bold-orange** headers above the targets that follow them.

### Template

```makefile
.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help message
	@printf "\n\033[1mProject — available targets:\033[0m\n"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@ / { printf "\n\033[1;38;5;208m%s\033[0m\n", substr($$0, 5); next } \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  \033[97m%-20s\033[0m %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
	@printf "\n"

##@ Quality

.PHONY: lint
lint:  ## Run the linter
	$(POETRY) run ruff check .

.PHONY: test
test:  ## Run the test suite
	$(POETRY) run pytest -q
```

### Rules

- **Every `.PHONY` target users should invoke** gets a `## Description` comment.
- **Internal/helper targets** (catch-alls, argument shims) get **no** `##` comment — that keeps them
  out of the help listing.
- The awk script preserves source order, so **order targets the way you want them to appear**. Never
  pipe the help listing through `sort` — it breaks source order and `##@` grouping.
- Target names use **lowercase kebab-case**: `build-docs`, `check-boundary`, `serve-docs`.
- Bump the `%-20s` column width if target names are longer.

---

## Standard Sections (in order)

1. **Header banner** — a file-level `# ── … ──` comment describing the Makefile (not rendered by help).
2. **Configuration variables** — `?=` for overridable tools (`POETRY ?= poetry`), `:=` for computed paths.
3. **Help target** — always first; the default goal.
4. **`##@` section headers** — group related targets (`Environment`, `Tests`, `Quality`, `Docs`, …).
5. **`check` / preflight target** — validate required tools are installed.
6. **Core targets** — install, test, lint, typecheck, clean, build, docs, etc.
7. **Catch-all** (optional) — `%:` with a `@:` recipe when the Makefile accepts positional arguments.

---

## Emoji Conventions

Use emoji as visual markers in `@echo` / `@printf` output to make progress scannable:

| Emoji | Meaning |
|-------|---------|
| ✅ | Success / check passed |
| ❌ | Failure / check failed |
| 🧪 | Running tests |
| 🧹 | Linting |
| 🔍 | Type-checking |
| 🧼 | Cleaning artifacts |
| 📦 | Building / packaging / installing |
| 🔄 | Reinstalling |
| 🪝 | Pre-commit hooks |
| 🔒 | Boundary / safety checks |
| 👷🏻 | Building docs |
| 🌐 | Serving / networking |

---

## ANSI Color Reference

| Code | Color | Usage |
|------|-------|-------|
| `\033[1m` | Bold | Help banner |
| `\033[0m` | Reset | End of styled text |
| `\033[1;38;5;208m` | Bold orange (256-color) | `##@` section headers |
| `\033[97m` | Bright white | Target names |
| `\033[32m` | Green | Success messages |
| `\033[31m` | Red | Error messages |

Orange headers + white target names is the standard scheme — warm, high-contrast, and readable on
both light and dark backgrounds. The `38;5;208` 256-color code works in every modern terminal.

---

## Anti-Patterns

- **No `##` comment on a user-facing target** — it won't appear in `make help`.
- **Piping the help listing through `sort`** — breaks source order and `##@` grouping.
- **`echo` for ANSI escapes** — behavior varies across shells; always use `printf`.
- **Hardcoding tool paths** — use `?=` so users can override (`POETRY ?= poetry`).
- **Spaces for recipe indentation** — Makefiles **require tabs**.
