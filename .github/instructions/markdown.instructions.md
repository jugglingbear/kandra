---
applyTo: "**/*.md"
description: "Markdown formatting rules (MyST-flavored, for the Sphinx docs site)."
---

# Markdown Standards

Documentation is authored in **Markdown (MyST-Parser)** and built by **Sphinx**. Keep Markdown clean
and consistently formatted.

---

## Line Length

- **120 characters maximum** (see `general.instructions.md` for whitespace/EOF rules).
- Reflow long lines at logical points (after sentences, before lists).

---

## Formatting Rules

### Headings

- **Surround headings with blank lines** (blank line before and after).

### Lists

- **Surround lists with blank lines** (blank line before the first item and after the last).

### Consecutive bold / key-value lines

- **Separate them with blank lines** if each should render on its own line. Adjacent lines with only a
  single newline merge into one paragraph — the most common rendering bug.

  ```markdown
  <!-- Correct: each renders on its own line -->
  **Transport:** BLE

  **Codec:** length-prefixed frame

  <!-- Incorrect: both merge into one paragraph -->
  **Transport:** BLE
  **Codec:** length-prefixed frame
  ```

### URLs

- **Use `[text](url)` link syntax**, not bare URLs.

### Code blocks

- Surround fenced code blocks with blank lines; always specify a language for syntax highlighting.

### Mermaid diagrams

- Diagrams render via `sphinxcontrib-mermaid` — use a fenced ` ```mermaid ` block.
- **Color-code authorship** to match the home-page image legend: user-authored nodes (manifest, handlers, codecs,
  app code) are bone tan; Kandra-supplied or generated nodes (generator, generated SDK, runtime) are Kandra blue.
  In flowcharts, declare and apply these classes:

  ```text
  classDef user fill:#d2be9a,stroke:#8a7650,color:#1a1a1a
  classDef kandra fill:#217eca,stroke:#14548a,color:#ffffff
  class nodeA,nodeB user
  class nodeC,nodeD kandra
  ```

  In sequence diagrams, group participants with `box rgba(210,190,154,0.3) Your code` and
  `box rgba(33,126,202,0.3) Kandra` (leave external actors such as the device unboxed). Only color a diagram that
  mixes both authorships.
- Mermaid label lines often exceed 120 characters; that is acceptable inside the fence. If a
  line-length linter is later added, wrap the block in `<!-- markdownlint-disable MD013 -->` /
  `<!-- markdownlint-enable MD013 -->` directives rather than globally relaxing the limit.

---

## MyST Notes

- Cross-references, admonitions, and directives use MyST syntax (e.g. a fenced ` ```{note} ` block).
  Prefer plain Markdown where possible; reach for MyST directives only when you need Sphinx features.
- Build the docs to catch errors: `make build-docs` (or `make build-docs-strict` for `-W` /
  warnings-as-errors).

---

## Quick Checklist

Before submitting Markdown:

- [ ] Lines ≤ 120 characters
- [ ] Headings surrounded by blank lines
- [ ] Lists surrounded by blank lines
- [ ] Fenced code blocks surrounded by blank lines, with a language tag
- [ ] URLs use `[text](url)` syntax
- [ ] `make build-docs-strict` passes for files under `docs/`
