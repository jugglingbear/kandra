# The Manifest

A manifest describes **one device**. A repo can hold any number of manifests side-by-side — typically one per device
under a shared `src/devices/<device_id>/` tree plus `src/common/` for things like codecs and transports that are reused
across devices. The generator is invoked once per (manifest, audience) pair and produces a distinct SDK package each
time, so N devices × M audiences = N × M shipped SDKs out of a single source repo.

Kandra ships with example manifests under `examples/`; the primary reference is the **Pneumatic Bear Poker**, a
fictional device whose sole job is to poke bears (pneumatically). It has one operational command (`poker.deploy`) and
one safety command (`safety.emergency_retract`), exposed over BLE and HTTP to two audiences: the internal team and the
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
  - id: ble
    adapter: devices.pneumatic_bear_poker.transports.ble:BleakAdapter
    codec: common.codecs.tlv:LengthPrefixedTLV
    capabilities: { mtu: 244, notifications: true }
  - id: http
    adapter: common.transports.http:HttpxAdapter
    codec: common.codecs.json:JsonCodec
    config: { base_url: "http://192.168.1.1:8080" }

commands:
  - id: poker.deploy
    handler: devices.pneumatic_bear_poker.handlers.poker:Deploy
    transports: [http, ble]
    audience: [internal, partner_woodland]
    timeout: 5.0
    idempotent: false

  - id: safety.emergency_retract
    handler: devices.pneumatic_bear_poker.handlers.safety:EmergencyRetract
    transports: [http, ble]
    audience: [internal, partner_woodland]
    timeout: 1.0
    idempotent: true

vendoring:
  # extra_include takes a file, a whole directory, or a glob (relative to a
  # source_root) -- for code or assets the static import walker can't discover.
  extra_include:
    - devices/pneumatic_bear_poker/handlers/super_important.py  # dynamically imported module
    - devices/pneumatic_bear_poker/assets/  # whole directory of runtime data files
  # Internal bench tooling -- keep it out of every shipped SDK.
  exclude: [devices/pneumatic_bear_poker/handlers/super_secret.py]
```

What the manifest does **not** contain: request/response field definitions, type schemas, business logic, or anything
else that would duplicate Python. Those live in the handler classes referenced by `handler:`.

The `vendoring` block tunes the import-closure walker. Each `extra_include` / `exclude` entry is a path relative to a
`source_root` and may be a single **file**, a whole **directory**, or a **glob** — never a dotted class name.
`extra_include` force-vendors code or data the static walker can't reach (a dynamically imported module, a directory of
runtime assets), and `exclude` drops files from the shipped SDK. This is a **planned** feature: the manifest accepts
these keys today, but the generator does not act on them yet.
