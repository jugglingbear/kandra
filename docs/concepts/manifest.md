# Manifest

A manifest describes **one device**. A repo can hold any number of manifests side-by-side — typically one per device
under a shared `src/devices/<device_id>/` tree plus `src/common/` for things like codecs and transports that are reused
across devices. The generator runs per manifest; building with `--profile <profile>` (a build profile from
[`audience_profiles.yaml`](audiences.md#two-layers-of-policy) that selects one or more audiences) emits one SDK per
(manifest, profile) pair, so N devices × M profiles = N × M shipped SDKs from a single source repo.

Kandra ships with example manifests under `examples/`; the primary reference is the **Pneumatic Bear Poker**, a
fictional device whose sole job is to poke bears (pneumatically). It talks HTTP over Wi-Fi and is built for two
[audiences](audiences.md): the internal team (`internal`) and an outside partner (`partner_woodland`).

**Commands**

- `poker.deploy` — extend the arm (100 psi max). *Audience:* internal, partner_woodland
- `poker.retract` — pull the arm back. *Audience:* internal, partner_woodland
- `poker.overdrive` — extend the arm past 100 psi, for bench testing. *Audience:* **internal only** — never in the
  partner's SDK
- `power.on` — power up the compressor. *Audience:* internal, partner_woodland
- `power.off` — power down the compressor. *Audience:* internal, partner_woodland
- `logs.download` — pull the device's log buffer. *Audience:* internal, partner_woodland.
  *[Capability](capabilities.md):* `diagnostics` — once you run a capability probe, the client refuses it on devices the
  probe says lack diagnostics

**Attributes**

- `settings.poke_intensity` — read or write the poke level (0–11). *Audience:* internal, partner_woodland
- `device.status` — read whether it's powered, whether the arm is out, and the pressure it's holding. *Audience:*
  internal, partner_woodland

*Audience* decides what goes into an SDK when it's built; *capability* decides what a connected device supports at run
time.

## Device State Machine

The Pneumatic Bear Poker's arm only moves while the unit is powered; arm commands sent while it's off are refused (HTTP
409, `REJECTED`).

```{mermaid}
stateDiagram-v2
    [*] --> OFF_RETRACTED
    OFF_RETRACTED --> ON_RETRACTED : power.on
    ON_RETRACTED --> OFF_RETRACTED : power.off
    ON_RETRACTED --> ON_EXTENDED : poker.deploy / overdrive
    ON_EXTENDED --> ON_EXTENDED : poker.deploy / overdrive
    ON_EXTENDED --> ON_RETRACTED : poker.retract
    ON_RETRACTED --> ON_RETRACTED : poker.retract
    ON_EXTENDED --> OFF_EXTENDED : power.off
    OFF_EXTENDED --> ON_EXTENDED : power.on
```

## Manifest Structure

The complete Pneumatic Bear Poker manifest, included straight from the example so it never drifts from the real file:

```{literalinclude} ../../examples/pneumatic_bear_poker/manifest.yaml
:language: yaml
:caption: examples/pneumatic_bear_poker/manifest.yaml
```

What the manifest does **not** contain: request/response field definitions, type schemas, business logic, or anything
else that would duplicate Python. Those live in the handler classes referenced by `handler:`.

`source_roots:` also bounds what an SDK can contain: every handler, codec, and adapter must live under a source root
or the build fails, and a `--profile` build vendors only files from under the roots.

Each entry under `transports:` declares one wire transport; its `family:` (`http`, `ble`, or `loopback`) decides which
per-operation wiring block is required.

- `codec:` is the dotted path to the user-types ↔ wire-envelope [codec](codec.md) — **required for every family except
  HTTP**, which uses the runtime `HttpJsonCodec` and so omits it (a transport-level `codec:` is ignored on HTTP;
  override it per command instead, below).
- `adapter:` (optional) selects the [Transport](transport.md) implementation: **omit it** to use the runtime default
  for the family (`HttpTransport` for HTTP, `BleTransport` for BLE), or point it at your own `package.module:Class` to
  plug in a custom transport — see
  [Custom transports via the manifest](transport.md#custom-transports-via-the-manifest).
- `channels:` (BLE only) names `(write, notify)` characteristic UUID pairs; each BLE command selects one by name.
- `enrollment: { login_path: /path }` (HTTP only, plus an optional `token_field`) is baked into a
  `<device>_sdk.http_enrollment()` factory (and the default for `discover_and_connect`), keeping the device's login
  endpoint out of caller code — see [Enrollment](enrollment.md#declaring-the-http-login-in-the-manifest).

Commands are one-shot actions; [attributes](attribute.md) are named device *state* you can read, write, and subscribe
to; [events](event.md) are stateless emissions you subscribe to. All three are pure wiring here — the payload types live
in the referenced handler classes. Every command, attribute, and event that rides an HTTP or BLE transport also needs
a per-transport `http:` / `ble:` wiring block — method/path, read/write/subscribe verbs, or a subscribe stream, as in
the `http:` blocks above (see also the [Attribute](attribute.md) / [Event](event.md) pages). A
command's wire block may also carry an optional `codec:` (a dotted `module:Class`) that overrides the transport's
default codec for that one command — the escape hatch when a single transport carries mixed wire formats (see
[Per-command codec override](codec.md#per-command-codec-override)). Any operation may also declare
`capabilities: [tag, ...]` to gate it behind [device capabilities](capabilities.md).

A command may declare `idempotent: true` when re-sending it is harmless (the device treats a repeat as a no-op). That
unlocks `retries: N` — after a *transient* transport failure (a dropped link or a timeout) the runtime re-sends the
command up to `N` more times before giving up. Non-idempotent commands are never auto-retried, so `retries: N` requires
`idempotent: true` (the loader rejects the pair otherwise). A stale-credential failure is *not* retried here — it
surfaces as [`IdentityStaleError`](lifecycle.md#credential-staleness--re-enrollment) for the client's re-enrollment
path instead. The [state machine](#device-state-machine) above shows how the reference device's flags were chosen.

The optional top-level `discovery:` block (at the end of the example) holds per-family match criteria: `ble:` (name
prefix, service UUIDs, manufacturer id) and/or `http:` (`base_urls` to probe, `probe_path`, optional
`server_header_prefix`). Each
family it declares must match a transport's `family:`, and the generator compiles it into the SDK's `scan_<family>()`
helpers and default matchers — see [Scanner](scanner.md#matcher).

The `vendoring` block tunes the import-closure walker. Each `extra_include` / `exclude` entry is a path relative to a
`source_root` and may be a single **file**, a whole **directory**, or a **glob** — never a dotted class name.
`extra_include` force-vendors code or data the static walker can't reach (a dynamically imported module, a directory of
runtime assets), and `exclude` drops files from the shipped SDK. The generator honors these during a `--profile`
(vendoring) build; a plain `kandra build` imports from `source_roots` and ignores them. See
[Audiences & IP Isolation](audiences.md).
