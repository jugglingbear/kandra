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

## Top-Level Keys

Every manifest is built from these keys. Each one has its own page listing every field it accepts. The manifest only
wires things together: request and reply types, and any logic, live in your Python handler classes.

| Key | Required | What it's for |
|-----|----------|---------------|
| [`schema_version`](manifest/schema_version.md) | Yes | The manifest format version; always `1` |
| [`device`](manifest/device.md) | Yes | The device's id, display name, and audiences |
| [`source_roots`](manifest/source_roots.md) | Yes | Folders holding your handler, codec, and other Python classes |
| [`transports`](manifest/transports.md) | Yes | How the SDK reaches the device (HTTP, BLE, …) |
| [`commands`](manifest/commands.md) | No | One-shot actions, such as `client.poker.deploy()` |
| [`attributes`](manifest/attributes.md) | No | Device state you read, write, or subscribe to |
| [`events`](manifest/events.md) | No | Things the device emits that you subscribe to |
| [`discovery`](manifest/discovery.md) | No | How `scan_http()` / `scan_ble()` find the device |
| [`vendoring`](manifest/vendoring.md) | No | Extra files to bundle into the SDK, or to leave out |

A manifest needs at least one command, attribute, or event. Your editor can autocomplete and check a manifest as you
write it; see [Editor Autocomplete](../reference/editor-autocomplete.md).

```{toctree}
:hidden:

manifest/schema_version
manifest/device
manifest/source_roots
manifest/transports
manifest/commands
manifest/attributes
manifest/events
manifest/discovery
manifest/vendoring
```

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
