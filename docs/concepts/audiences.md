# Audiences &amp; IP Isolation

Kandra's headline feature is **white-label SDK generation**: one source repo, one manifest, and *N* self-contained SDKs
&mdash; one per audience &mdash; where a partner's artifact contains zero trace of anything it isn't entitled to see. This
page covers how that works: audience profiles, per-file overrides, the vendoring pipeline, the leakage scan, and the
`kandra audit` command.

```{note}
Vendoring is **opt-in per build**. `kandra build manifest.yaml` (no profile) keeps the lightweight development behaviour:
the generated client *imports* your handlers from `source_roots`. Passing `--profile <name>` switches on the full
prune &rarr; vendor &rarr; scan pipeline and emits a package that depends only on `kandra-runtime`.
```

## Two layers of policy

Every source file has a **default audience of `internal`**. Authors must consciously opt a file into a wider audience,
and two layers cooperate to decide what ships:

1. **`audience_profiles.yaml`** &mdash; the repo-level policy, living next to the manifest. It declares the build
   *profiles* and a **static** path&nbsp;&rarr;&nbsp;audience-tags map. Because InfoSec gets a single auditable file
   listing every file that escapes `internal`, the map is static-paths-only: a glob metacharacter is a hard error, so a
   rename can never silently re-categorize a file.
2. **Per-file `# kandra-audience:` header** &mdash; an in-file override that may **narrow** (never **widen**) a file's
   YAML grant.

```yaml
# audience_profiles.yaml
profiles:
  internal:
    description: "Full internal build — nothing pruned."
    include_audience: [internal, partner_woodland]
    deny_substrings: []
  partner_woodland:
    description: "Release for Woodland Robotics (external partner)."
    include_audience: [partner_woodland]
    deny_substrings:                 # must never appear in the shipped tree
      - super_secret
      - dump_internal_registers
      - "@bearcorp.internal"

# Static path -> audience-tags map. Files absent here default to [internal].
files:
  src/common/codecs/tlv.py:                                  [internal, partner_woodland]
  src/devices/pneumatic_bear_poker/handlers/poker.py:        [internal, partner_woodland]
  src/devices/pneumatic_bear_poker/handlers/diagnostics.py:  [internal, partner_woodland]
  # ... every file a partner_woodland command can reach must be granted the tag
```

### How a file's effective audience is resolved

For each file the generator computes **YAML grant &cap; header override**:

```{list-table}
:header-rows: 1
:widths: 34 32 34

* - YAML grant
  - `# kandra-audience:` header
  - Effective audience
* - *(file not listed)*
  - *(absent)*
  - `internal` (default)
* - `[a, b, c]`
  - *(absent)*
  - `[a, b, c]`
* - `[a, b, c]`
  - `# kandra-audience: a`
  - `[a]` (narrowed)
* - `[a]`
  - `# kandra-audience: b`
  - **build error** &mdash; a header cannot widen the grant
```

A file is kept for a profile when its effective audience intersects that profile's `include_audience`.

## The build pipeline

```{mermaid}
flowchart TB
    prof["audience_profiles.yaml<br/>(--profile partner_woodland)"] --> prune
    manifest["manifest.yaml"] --> prune["1. Prune commands<br/>by include_audience"]
    prune --> closure["2. Walk handler/codec/model<br/>import closure (AST)"]
    closure --> filter["3. Audience-filter every file<br/>(grant ∩ header)"]
    filter --> vendor["4. Vendor + rewrite imports<br/>into &lt;pkg&gt;/_internal/"]
    vendor --> scan["5. Leakage scan<br/>(deny_substrings)"]
    scan --> verify["6. Import-check with<br/>source roots OFF sys.path"]
    verify --> out["dist/partner_woodland/<br/>&lt;device&gt;_sdk/"]
```

Two guards **fail the build loudly** rather than shipping a leak:

- A **kept** file that imports an audience-**dropped** file &mdash; the error names the exact chain.
- A surviving command whose own handler / model / codec file was dropped &mdash; you granted the command to an audience
  but not the code it needs.

The final **import-check runs with the source roots removed from `sys.path`**, so if any vendored file still reaches
back into the authoring tree (a stray dynamic import), the build fails instead of shipping a package that only works on
the author's machine.

## Vendoring &amp; the namespace rewrite

Surviving files are copied under `<package>/_internal/`, preserving each file's path relative to its source root. A
module authored as `common.codecs.tlv` lands at `<package>/_internal/common/codecs/tlv.py` and is importable as
`<package>._internal.common.codecs.tlv`. All source roots coexist under the single `_internal` subpackage.

Import rewriting is **textual, not AST-round-tripped** &mdash; only the module reference inside each absolute
`import` / `from` statement is edited, so comments (including `# kandra-audience:` headers), formatting, and docstrings
survive verbatim. Relative imports (`from . import x`) are left untouched because the package tree is preserved.

```{note}
`vendoring.extra_include` and `vendoring.exclude` in the manifest tune the closure walk itself &mdash; force-adding a
dynamically imported module or a directory of runtime assets, or dropping internal bench tooling. See
[The Manifest](manifest.md) for the field shapes.
```

## The leakage scan

After vendoring, the emitted tree is scanned for every `deny_substrings` entry in the active profile &mdash; a plain
case-sensitive substring test (mirroring `grep`) across all text files, including user-visible metadata like
`_generated_from.json`. Any hit fails the build. Binary assets and `__pycache__` are skipped.

## Auditing a profile

`kandra audit` answers &ldquo;what ships, and what's stale?&rdquo; for a profile without generating anything &mdash; the
command InfoSec runs before signing off a release:

```console
$ kandra audit manifest.yaml --profile partner_woodland
audit profile=partner_woodland include_audience=['partner_woodland']

  + src/devices/pneumatic_bear_poker/handlers/poker.py  [internal, partner_woodland]
  - src/devices/pneumatic_bear_poker/handlers/super_secret.py  [internal]
```

Each `.py` file under the source roots is printed with `+` (included for this profile) or `-` (pruned), plus its
resolved effective audience. A trailing section lists **stale** `files:`-map entries &mdash; paths that no longer resolve
to a real file, so the referenced file has silently fallen back to the default `internal` grant.

## Provenance

Every generated package carries a `_generated_from.json` recording the profile, the SHA-256 of both the manifest and
`audience_profiles.yaml`, the Kandra version, and a UTC timestamp &mdash; an audit trail you can ship to a partner
without exposing the source repo.

## Try it

```console
# Full internal build — nothing pruned.
$ kandra build examples/pneumatic_bear_poker/manifest.yaml --profile internal --clean

# Partner release — internal-only files pruned, leakage-scanned, self-contained.
$ kandra build examples/pneumatic_bear_poker/manifest.yaml --profile partner_woodland --clean
```

The partner artifact vendors the reference SDK under `_internal/`, drops the internal-only bench module
(`super_secret.py`), passes the leakage scan, and imports cleanly with the source tree off the path.
