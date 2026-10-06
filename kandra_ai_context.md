# Kandra — Recovered AI Working Context

> Reconstructed from lost session `f1383fab-7ba5-4c64-88e1-61ed84e28315`
> (137 user turns, 2026-09-10 → 2026-09-17) plus the 2026-09-21 recovery session
> `18fcfd30`. Cross-checked against the current `main` (HEAD `6c90f5b`,
> *"docs: correct Getting Started inaccuracies"*). Raw transcripts live in
> `temp/session-f1383fab-conversation.md` (clean prose) and
> `temp/session-f1383fab-recovered.md` (with tool calls).

This file captures the **decisions, rationale, and state** from that long session so
work can resume without re-deriving anything. For repo structure / conventions, read
`AGENTS.md` first; for remaining work, see `roadmap.html`.

---

## 1. Where we left off (the open thread)

The very last request (turn 137) was a **design discussion, not yet implemented**:

> "Do we have any docs/examples for how to set up your own project? … `git init`,
> configure Poetry, add the Kandra PyPI runtime, organize files, create handlers…
> Should we recreate the Pneumatic Bear Poker or make a new example? Let's talk
> design before we implement anything."

### The key realization
`kandra create-sdk <dir>` **already scaffolds a working Poetry project**: `pyproject.toml`
(with `kandra-runtime` wired in), `Makefile`, a `kandra.yaml` manifest with a stub `ping`
command, `src/<pkg>/{handlers,codecs,transports}/` stubs, a smoke test, and
`examples/connect.py`. Interactive wizard **or** `--non-interactive --answers answers.yaml`.
So a "build your own" guide should be built **around the scaffolder**, not hand-recreate
Bear Poker.

### Proposed (not yet decided/built) — `docs/build-your-own.md`
Recommended arc: prereqs → `git init` + `kandra create-sdk my-thing` (tour output) →
`poetry install` + `make validate` → write first real handler (replace `ping`) + wire
manifest → `kandra build` (inspect typed client) → run against a tiny Flask sim → "where
to next" (point at Bear Poker for audiences/enrollment/capabilities).

**Three open questions awaiting the user's call before implementing:**
1. New tiny fictional device via `create-sdk`, or recreate Bear Poker? (AI leaned: **new tiny device**.)
2. New `docs/build-your-own.md` page, or fold into `getting-started.md`? (AI leaned: **new page**.)
3. Ship a runnable `examples/` dir for it, or docs-only steps the reader types? (undecided)

Also flagged: **`create-sdk` currently has zero reference docs** (`docs/reference/generator.md`
omits it) — the guide would close that gap.

---

## 2. Roadmap status (from `roadmap.html`)

| Milestone | State | Notes |
|---|---|---|
| M0–M8.5 | ✅ Done | Foundation, transports, dispatch, audience pruning + vendoring + leakage scan (M8), enrollment shortcut. |
| **M9** — capabilities + attributes + events | ✅ Done | `discover_capabilities()`, typed `CapabilityUnavailableError`, Attribute/Event facades. |
| **M10** — credential lifecycle | ✅ Done ("Just shipped") | See §4. |
| **M11** — codegen enrollment helpers | 🟡 Candidate (likely skip) | Superseded by `discover_and_connect()`. Keep only if a declarative `enrollment:` block proves it shrinks the adapter constructor. |
| **M12** — generated-SDK device reference (docs) | 📋 Planned | `kandra build --docs` emits a Markdown (primary) + optional single-file HTML device reference from the **pruned** profile (audience-scoped, leak-free). Do **not** reinvent `autodoc`/`pdoc` for the Python API. |
| **M99** — strip roadmap breadcrumbs | 📋 Planned, **do last** | Remove `M<n>` / "slice 9x" from source/test docstrings + example comments. Keep milestone numbers only in `roadmap.html` and `docs/design-review.md`. |

**Loose ends — both RESOLVED:**
- `idempotent` / `retries` command fields: now wired end-to-end (retries re-send up to N
  times after transient failure; loader requires `idempotent: true` for `retries > 0`).
  Fixing this surfaced + fixed a latent bug where `dispatch()` flattened `IdentityStaleError`
  into a generic failure (so mid-session recovery now actually fires).
- Attributes/events interim guard: both primitives wired (9b/9c), generate working facades,
  loader validates per-transport subscribe wiring.

**Deferred (intentional):**
- `Transport.stream()` — reserved for bulk byte downloads; kept off the shipped protocol
  until a real feature needs it.
- Structured logging — runtime uses stdlib `logging` with namespaced loggers
  (`kandra.transport.ble`, `kandra.command.<id>`); revisit richer logger later.

**Non-goals:** no GUI manifest editor, no runtime YAML in generated SDK, no DSL/expression
language in YAML, no daemon/RPC mode, no firmware-stub generation.

---

## 3. Architecture decisions clarified this session

### Vendoring & imports (M8) are NOT audience-conditional
- **Today's model** the session moved away from: generator emitted the user's real
  source-tree paths (e.g. `from pneumatic_bear_poker.models import DeployRequest`),
  which leaks package names and isn't self-contained.
- **M8 vendoring**: (1) copy the transitive import closure into
  `dist/<profile>/<sdk>/_internal/…`; (2) rewrite the top-level package prefix
  **uniformly** (`pneumatic_bear_poker.*` → `<sdk>._internal.*`). Imports stay ordinary,
  static, non-dynamic. **Audience only affects _which files/commands_ land in a build**,
  never the _form_ of an import. Each `--profile` gets its own `dist/<profile>/` tree.

### Source-root constraint (decided in turn 91)
User code (handlers/codecs) **must live under the `source_roots` paths** in the manifest.
No reaching outside. A shared/common repo can be added as another source root. The closure
walk is restricted to `source_roots` (never stdlib/third-party/unlisted). A `--dest` /
external-repo-output feature was discussed but **not adopted**.

### Closure error must be real (turn 89 — important lesson)
A docstring claimed `ClosureError` was raised for an unresolved entry point, but the code
**silently skipped it**. This was treated as a serious defect. Fixed via a new
`resolve_entry_points()` helper in `closure.py` that actually raises `ClosureError`. This
triggered a **full docs factual-accuracy audit** (turn 90) — the standing rule now: **docs
must not claim behavior the code doesn't actually do.**

### Capability negotiation semantics (M9)
An operation is available **iff `op.capabilities ⊆ device_tags`** — pure set-containment,
Kandra never interprets the strings. Capabilities are **per-command**, and the model is
**allow-by-default** (a command with no `capabilities` is always available).

### Audience model (decided, kept as-is)
- `audience:` stays in the **manifest** and is **required per command/attribute/event**
  (each must be a subset of `device.audience`). A device-level default (e.g.
  `default_commands_to_audience`) was considered and **rejected as too subtle for v1** — too
  easy for a user to accidentally over-expose.
- Attributes and events **do** carry `audience:` in the manifest.

### Per-command codec override (implemented mid-session)
Commands can specify a `codec:` (dotted `module:Class`) that overrides the transport default
for that command only — needed because e.g. Open GoPro has different request/response bodies
per command. HTTP commands additionally have `body_codec` / `response_codec` (`json`|`none`).
The user rejected introducing a short-lived limitation: **spec + docs + implement now**,
don't stub it on the roadmap.

### Declarative HTTP enrollment (implemented)
Manifest HTTP transport supports `enrollment: { login_path: /v1/auth/login }`. The generated
SDK bakes this into `http_enrollment()`; runtime payloads injected via the generated
`<family>_enrollment(login_payload=...)` factory. Validated as **HTTP-only** (loader rejects
`enrollment` on non-HTTP families). This removed magic strings like `/v1/auth/login` from
demo code.

---

## 4. M10 — Credential lifecycle (shipped this session)

A saved identity that stops authenticating is now a **recoverable** condition, at connect
time and mid-session:

- `IdentityStaleError` (a recoverable `TransportError`). HTTP transport maps `401`/`403` to
  it by default (opt out via `stale_statuses`); a BLE adapter raises it for a dead bond.
- `Identity.last_validated` timestamp, stamped on every successful connect and re-stamped
  after a mid-session refresh.
- `re_enroll(saved_name, enrollment=...)` — re-scans, re-enrolls, atomically overwrites the
  saved record. `connect(saved_name, on_stale=...)` recovers a stale bond at `open()` and
  retries once.
- **Mid-session auto-refresh**: `connect(..., on_stale=..., refresh_mid_session=True)` wraps
  dispatch so a stale error _after_ connect re-enrolls, rebuilds transports in place, and
  retries the command once. **Off by default**, toggled independently of connect-time
  `on_stale` — so a **connectivity/auth test harness sees the raw `IdentityStaleError`**
  instead of silent recovery (this opt-out was an explicit user requirement, turn 19–20).

Design detail: `docs/concepts/lifecycle.md`.

---

## 5. Examples & the Pneumatic Bear Poker (NBP)

- **NBP is now HTTP-only** (turns 36–39). BLE transport, all `ble:` command/attribute/event
  blocks, and `discovery.ble` were removed so new users aren't confused into thinking BLE is
  required (there's no real BLE demo target). **BLE generator/codegen test coverage was
  preserved by moving it to a dedicated test fixture** (Option A), not deleted.
- **Open GoPro** example exists (HTTP + BLE) and was **renamed `gopro` → `opengopro`**
  (turns 48–49) to make clear only open, non-proprietary Open GoPro data is used. Real
  support is a future partial goal.
- **`hello_world`** is the trivial buildable-in-5-min example.
- Every example now has a consistent **`demo.py`** (turns 63–65). Demos take a `--url` flag;
  demo comments reflow at **120 chars** (turn 119).
- **Firmware simulator**: NBP has a Flask sim (`examples/pneumatic_bear_poker/firmware_sim/`,
  also a Dockerfile) implementing every HTTP endpoint (commands, discovery probe, enrollment
  login). Docker + direct-run are presented together as two ways to start the sim (turn 131).
- Running the demo needs `PYTHONPATH="examples/pneumatic_bear_poker/dist:examples/pneumatic_bear_poker/src"`
  (each example builds into its own `examples/<device>/dist/` — the `kandra build` default output dir).
- **Port pain (recurring):** VS Code / the remote server squats on ports (8080, then 48080).
  Resolution: pick a high uncommon default, make the port explicit via `PORT=` env for the
  sim and `--url` for the demo, and **drop the "if it doesn't work…" hedging** from docs —
  just give the explicit syntax. Several stray dev processes were cleaned up.
- **Identity store** = `PlatformDirsJsonStore`, backed by the `platformdirs` package
  (standard cross-platform user-data dirs on Linux/macOS/Windows). Clearing a saved identity:
  `PlatformDirsJsonStore(app_name="my_device_sdk").delete("my_device")`. That "clearing"
  guidance was moved to `docs/concepts/identity.md`. Use generic names like `my_device` in
  docs — **"kitchen bear" / "kitchen" was rejected** as confusing/context-free (turns 107, 128).

---

## 6. Docs & README work

- **`kandra.md` was replaced by `roadmap.html`** at the repo root (turns 6, 44–45). The
  roadmap holds **only incomplete milestones**; every completed milestone gets real docs
  under `docs/`. `kandra.md` was deleted after porting.
- Sphinx + MyST docs site under `docs/` (concepts/ + reference/), Furo theme,
  autodoc-pydantic, mermaid.
- **Manifest reference docs are auto-enriched from the pydantic models** — the page renders
  types/constraints/validators/class-docs; a pass added per-field prose descriptions to
  ~40 fields (turns 63, 66).
- **"Why Kandra?" / motivation** page: reworked the "How It Works" diagram (stacked less
  horizontally for legible text), fixed misleading phrasing ("You refactor with the IDE"
  scared users; "no generated code to read" was false since the SDK _is_ generated). Clarified
  that **attributes = device state** (added verbiage), and that `device.yaml` in the diagram
  = the manifest.
- **Docstring/interface note:** user-written handler/codec classes **do** implement runtime
  `typing.Protocol` interfaces (Transport, Codec, Command/dispatch) — a real common surface,
  not loose duck-typing.
- Code-block comments in docs should be **prettily aligned** (turn 73).
- Strict factual-accuracy audits were run on the concept docs and the Getting Started page;
  fixed drift around nonexistent `retries`/`request_filter`/`Result` alias claims and a
  stray empty code block.
- README "What you bring" / "Quickstart" were refreshed and made **generic** (no NBP-specific
  imports); explicit inline-comment imports distinguishing `kandra_runtime` (ships with
  Kandra) vs the generated SDK.

---

## 7. Working environment gotchas

- Development is over **VS Code Remote-SSH** (`code --remote ssh-remote+sfoley@192.168.0.243
  /Users/sfoley/repos/kandra/`). The AI runs remotely; **browser/HTML preview and some `gh`
  auth over SSH can misbehave** — when the AI can't view rendered HTML, it makes diagram
  changes and asks the user to look.
- **Session loss is recurring** — VS Code scopes chat history per workspace-storage hash; a
  changed remote authority/path yields a new hash and an empty sidebar list, while the
  chronicle session store keeps its own copy. Recover via the session store SQL + the
  transcript dumper at `temp/recover_transcript.py`.
- Quality gate: **`make qa`** (check-boundary + lint + typecheck + check-schema + test).
  Run `make schema` after changing manifest pydantic models. Scratch files go in `temp/`.

---

## 8. Suggested next actions on resume

1. Get the user's calls on the three **build-your-own guide** questions in §1, then implement
   (likely: new tiny `create-sdk`-based device + new `docs/build-your-own.md` + document
   `create-sdk`, which currently has no reference docs).
2. Optionally proceed to **M12** (generated-SDK device reference via `kandra build --docs`).
3. **M11** most likely gets closed as skipped/superseded unless a declarative `enrollment:`
   survey justifies it.
4. **M99** breadcrumb scrub — only after M11/M12 resolve.
