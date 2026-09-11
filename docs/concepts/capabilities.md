# Capability Negotiation

Not every device supports every operation the SDK knows about — firmware variants, hardware tiers, or a half-finished
prototype may expose only a subset. **Capability negotiation** lets a client learn what the *connected* device actually
supports and **fail fast, locally**, instead of firing a doomed request and interpreting a wire error.

```python
await client.discover_capabilities(probe)          # ask the device once, cache the answer
await client.logs.download(DownloadLogsRequest())  # raises CapabilityUnavailableError if unsupported
```

## Opaque tags, set-containment, allow-by-default

An operation (command / [attribute](attribute.md) / [event](event.md)) may declare the capability **tags** it requires:

```yaml
commands:
  - id: logs.download
    capabilities: [diagnostics]   # only firmware with the diagnostics package
    # ...
```

- Tags are **opaque strings you choose** (`diagnostics`, `gps`, `hero11_plus`, …). Kandra never interprets them.
- A device reports the set of tags it supports (via a probe, below). An op is available iff
  `op.capabilities ⊆ device_tags` — pure set-containment.
- **Allow-by-default:** an op with no `capabilities:` is never gated. You tag only the operations that actually vary
  across devices.

### Granularity is your choice

Because gating is set-containment over tags *you* choose, you decide the grain:

- **Feature tag** — one tag on several ops that ship together (`capabilities: [gps]`). The device advertises `gps` once
  and all of them light up.
- **Per-operation tag** — when a device might support one operation of a feature but not another (a prototype,
  mid-development firmware), give each op its own tag. The natural choice is the **operation id itself**:

  ```yaml
  - id: camera.op_x
    capabilities: [camera.op_x]
  - id: camera.op_y
    capabilities: [camera.op_y]
  ```

  A probe returning `{camera.op_x}` leaves `op_x` available and `op_y` gated — even though they belong to the same
  conceptual feature.

## The probe is yours to write

Kandra imposes **no capabilities format on the device**. A `CapabilityProbe` is a device-specific adapter *you*
implement — the same model as codecs, transport adapters, and enrollment adapters. It returns the tag set the connected
device supports, determined however suits the device:

```python
from kandra_runtime import StaticCapabilityProbe

# 1. No capabilities endpoint (e.g. an action camera) — hardcode from a published
#    spec, optionally keyed on a value you read with a normal command.
class MyCameraProbe:
    async def probe(self, client: MyCameraClient) -> frozenset[str]:
        state = await client.camera.get_state(GetState())
        return _TAGS_BY_MODEL[state.data.model]

# 2. A device that *does* expose capabilities — query it through the client.
class AcmeProbe:
    async def probe(self, client: AcmeClient) -> frozenset[str]:
        info = await client.system.info(InfoRequest())
        return frozenset(info.data.features)

# 3. Known statically / tests — the built-in convenience probe.
caps = await client.discover_capabilities(StaticCapabilityProbe("diagnostics", "gps"))
```

The probe receives the client, so it may dispatch ordinary operations to interrogate the device. During discovery
nothing is gated yet, so the probe's own calls run freely.

## Lifecycle & error

```{list-table}
:header-rows: 1

* - Stage
  - Behaviour
* - Before `discover_capabilities()`
  - Nothing is gated — every operation dispatches normally.
* - After `discover_capabilities(probe)`
  - The device's tag set is cached; a gated op whose tags aren't all present raises
    {class}`~kandra_runtime.errors.CapabilityUnavailableError` locally.
```

`CapabilityUnavailableError` carries the offending `operation_id` and the still-`missing` tags:

```python
try:
    await client.logs.download(DownloadLogsRequest())
except CapabilityUnavailableError as exc:
    print(exc.operation_id, exc.missing)   # 'logs.download' ('diagnostics',)
```

Both facades expose it: `await client.discover_capabilities(probe)` (async) and
`client.discover_capabilities(probe)` (sync wrapper). Only operations that declare capabilities generate any of this
machinery — a manifest that gates nothing produces a byte-identical, capability-free client.

## Under the hood

The generator bakes a `_CAPABILITIES` table (op id → required tags) into the client and checks it at the single dispatch
chokepoints (`_dispatch` / `_dispatch_subscribe`) after discovery. See the reference SDK's `logs.download`
(`capabilities: [diagnostics]`) in the `pneumatic_bear_poker` example.
