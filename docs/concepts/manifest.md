# The Manifest

A manifest describes **one device**. A repo can hold any number of manifests side-by-side — typically one per device
under a shared `src/devices/<device_id>/` tree plus `src/common/` for things like codecs and transports that are reused
across devices. The generator runs per manifest; building with `--profile <audience>` emits one SDK per (manifest,
audience) pair, so N devices × M audiences = N × M shipped SDKs from a single source repo.

Kandra ships with example manifests under `examples/`; the primary reference is the **Pneumatic Bear Poker**, a
fictional device whose sole job is to poke bears (pneumatically). It has commands (`poker.deploy`,
`safety.emergency_retract`), one settable [attribute](attribute.md) (`settings.poke_intensity`), and one
[event](event.md) (`alerts.bear_stirred`), exposed over HTTP to two audiences: the internal team and the
`partner_woodland` partner.

A minimal manifest looks like this (see the full
[Pneumatic Bear Poker example](https://github.com/jugglingbear/kandra/tree/main/examples/pneumatic_bear_poker) for the
complete version):

```yaml
schema_version: 1

device:
  id: pneumatic_bear_poker
  display_name: Pneumatic Bear Poker
  firmware_min: "2.4.0"
  audience: [internal, partner_woodland]

source_roots:
  - src

transports:
  - id: http
    family: http
    config: { base_url: "http://192.168.1.1:8080" }
    # HTTP needs no codec (built-in JSON) and no adapter (runtime HttpTransport) --
    # both are optional here. BLE / serial transports do declare a codec.

commands:
  - id: poker.deploy
    handler: devices.pneumatic_bear_poker.handlers.poker:Deploy
    transports: [http]
    audience: [internal, partner_woodland]
    timeout: 5.0
    idempotent: false

  - id: safety.emergency_retract
    handler: devices.pneumatic_bear_poker.handlers.safety:EmergencyRetract
    transports: [http]
    audience: [internal, partner_woodland]
    timeout: 1.0
    idempotent: true
    retries: 2   # safe to re-send on a transient link drop

attributes:
  # Named device *state* (read / write / subscribe), as opposed to a one-shot
  # command. Emitted as `client.settings.poke_intensity.read()/.write(v)/.subscribe()`.
  - id: settings.poke_intensity
    handler: devices.pneumatic_bear_poker.handlers.settings:PokeIntensitySetting
    transports: [http]
    operations: [read, write, subscribe]
    audience: [internal, partner_woodland]

events:
  # Stateless emission (subscribe-only). Emitted as
  # `client.alerts.bear_stirred.subscribe()` (async-only).
  - id: alerts.bear_stirred
    handler: devices.pneumatic_bear_poker.handlers.alerts:BearStirredAlert
    transports: [http]
    audience: [internal, partner_woodland]

vendoring:
  # extra_include takes a file, a whole directory, or a glob (relative to a
  # source_root) -- for code or assets the static import walker can't discover.
  extra_include:
    - devices/pneumatic_bear_poker/handlers/super_important.py  # dynamically imported module
    - devices/pneumatic_bear_poker/assets/                      # whole directory of runtime data files
  # Internal bench tooling -- keep it out of every shipped SDK.
  exclude: [devices/pneumatic_bear_poker/handlers/super_secret.py]
```

What the manifest does **not** contain: request/response field definitions, type schemas, business logic, or anything
else that would duplicate Python. Those live in the handler classes referenced by `handler:`.

Each entry under `transports:` names a wire channel. `codec:` is the dotted path to the user-types ↔ wire-envelope
[codec](codec.md) — **required for every family except HTTP**, which always uses the runtime `HttpJsonCodec` and so
may omit it. `adapter:` (optional) selects the [Transport](transport.md) implementation: **omit it** to use the runtime
default for the family (`HttpTransport` for HTTP, `BleTransport` for BLE), or point it at your own `package.module:Class`
to plug in a custom transport — see
[Custom transports via the manifest](transport.md#custom-transports-via-the-manifest). An HTTP transport may also
declare `enrollment: { login_path: /path }`, which the generator bakes into a `<device>_sdk.http_enrollment()` factory
(and the default for `discover_and_connect`), keeping the device's login endpoint out of caller code — see
[Enrollment](enrollment.md#declaring-the-http-login-in-the-manifest).

Commands are one-shot actions; [attributes](attribute.md) are named device *state* you can read, write, and subscribe
to; [events](event.md) are stateless emissions you subscribe to. All three are pure wiring here — the payload types live
in the referenced handler classes. (Every command, attribute, and event that rides an HTTP or BLE transport also needs
a per-transport `http:` / `ble:` wiring block — method/path, read/write/subscribe verbs, or a subscribe stream — all
elided above for brevity; see the full example and the [Attribute](attribute.md) / [Event](event.md) pages.) A
command's wire block may also carry an optional `codec:` (a dotted `module:Class`) that overrides the transport's
default codec for that one command — the escape hatch when a single transport carries mixed wire formats (see
[Per-command codec override](codec.md#per-command-codec-override)). Any operation may also declare
`capabilities: [tag, ...]` to gate it behind [device capabilities](capabilities.md).

A command may declare `idempotent: true` when re-sending it is harmless (the device treats a repeat as a no-op). That
unlocks `retries: N` — after a *transient* transport failure (a dropped link or a timeout) the runtime re-sends the
command up to `N` more times before giving up. Non-idempotent commands are never auto-retried, so `retries: N` requires
`idempotent: true` (the loader rejects the pair otherwise). A stale-credential failure is *not* retried here — it
surfaces as [`IdentityStaleError`](lifecycle.md) for the client's re-enrollment path instead.

The `vendoring` block tunes the import-closure walker. Each `extra_include` / `exclude` entry is a path relative to a
`source_root` and may be a single **file**, a whole **directory**, or a **glob** — never a dotted class name.
`extra_include` force-vendors code or data the static walker can't reach (a dynamically imported module, a directory of
runtime assets), and `exclude` drops files from the shipped SDK. The generator honors these during a `--profile`
(vendoring) build; a plain `kandra build` imports from `source_roots` and ignores them. See
[Audiences & IP Isolation](audiences.md).
