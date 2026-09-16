# Attribute

An **Attribute** is a named piece of device *state* — something you can `read`, `write`, and (optionally) `subscribe`
to. It complements the [Command](manifest.md) primitive: a command is a one-shot *action* (`poker.deploy`), while an
attribute is durable *state* (`settings.poke_intensity`). The name follows device-modeling convention — a BLE GATT
*attribute*, or an attribute on a Matter/Zigbee cluster: a value the firmware exposes, **not** a Python object field.

The generator emits a namespaced sub-object on the client, so an attribute reads like an ordinary property with typed
methods:

```python
level = await client.settings.poke_intensity.read()          # Result[PokeIntensity] | None
await client.settings.poke_intensity.write(PokeIntensity(9))  # Result[...] | None
async for update in client.settings.poke_intensity.subscribe():
    print(update.data)                                        # one Result per change
```

## Handler Contract

An attribute handler is a plain class that declares the payload types the generator wires up. There is no method body —
the handler is *data*, exactly like a command handler:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class PokeIntensity:
    """How hard the arm pokes, on a 0–11 scale."""

    level: int


class PokeIntensitySetting:
    """`settings.poke_intensity` — read / write / subscribe the poke level."""

    value = PokeIntensity
```

The two recognised class attributes are:

```{list-table}
:header-rows: 1

* - Attribute
  - Required?
  - Role
* - `value`
  - Yes
  - The payload returned by `read()`, pushed by `subscribe()`, **and** accepted as the `write()` input.
* - `write_ack`
  - No
  - The payload returned by `write()`. Defaults to `value` — most devices echo the value they just accepted.
```

Because `read`/`subscribe` take no arguments, their request is the empty {class}`~kandra_runtime.NoArgs` sentinel; you
never construct it yourself.

## Operations & Wiring

Each attribute lists its `operations` (any non-empty subset of `read`, `write`, `subscribe`) and a per-transport wiring
block. Every declared operation must be wired on every transport the attribute rides — the manifest loader enforces this.

```yaml
attributes:
  - id: settings.poke_intensity
    handler: devices.pneumatic_bear_poker.handlers.settings:PokeIntensitySetting
    transports: [http]
    operations: [read, write, subscribe]
    audience: [internal, partner_woodland]
    http:
      http:
        read:  { method: GET, path: /v1/settings/poke_intensity }
        write: { method: PUT, path: /v1/settings/poke_intensity, body_codec: json }
        subscribe: { mode: sse, path: /v1/settings/poke_intensity/events }
```

Verbs are **explicit**, never inferred — a device that "writes" via `GET /setting?option=9` sets `method: GET` with
`query_from_request: true`, exactly like a command. This keeps non-REST firmware first-class.

## Subscribe Delivery Modes

`subscribe()` is backed by the transport's [`Subscribable`](transport.md) capability. How each
update arrives depends on the wiring:

```{list-table}
:header-rows: 1

* - Mode
  - Declared as
  - Behaviour
* - `sse`
  - `subscribe: { mode: sse, path: ... }`
  - Native HTTP server-sent events — one long-lived stream, one `Result` per `data:` frame.
* - `poll`
  - `subscribe: { mode: poll, path: ..., interval: 2.0 }`
  - The client re-reads the endpoint every `interval` seconds. **Opt-in only** — `poll` *requires* an `interval`,
    so polling is never a silent default.
* - `ble`
  - `ble: { channel: ... }`
  - The BLE channel's notify characteristic; one `Result` per notification.
```

Whichever mode is wired, the caller sees the same thing: an `AsyncIterator[Result[value]]`. Polling versus native push is
a deployment detail, not an API change.

## Async vs Sync Facades

The client ships both an async facade and an `asyncio.run`-wrapping sync facade. `read` and `write` exist on both;
**`subscribe` is async-only** — a synchronous iterator over a long-lived push stream would have to block a thread, which
the sync wrapper deliberately does not do.

```{list-table}
:header-rows: 1

* - Operation
  - Async facade
  - Sync facade
* - `read`
  - `await client.settings.poke_intensity.read()`
  - `client.settings.poke_intensity.read()`
* - `write`
  - `await client.settings.poke_intensity.write(v)`
  - `client.settings.poke_intensity.write(v)`
* - `subscribe`
  - `async for r in client.settings.poke_intensity.subscribe(): ...`
  - *(not generated — async only)*
```

## Under the Hood

Attributes reuse the [Command](manifest.md) machinery: each operation is compiled to a synthetic command
(`settings.poke_intensity.read`, `.write`, `.subscribe`) in the generated registry, so the same
[Codec](codec.md) / [Result](result.md) / dispatch path carries it.

```{mermaid}
flowchart LR
    read["client.settings.poke_intensity.read()"] --> disp["dispatch"]
    write["client.settings.poke_intensity.write(v)"] --> disp
    sub["client.settings.poke_intensity.subscribe()"] --> subdisp["dispatch_subscribe"]
    disp --> codec[Codec]
    subdisp --> codec
    codec --> transport[Transport]
    transport --> device[Device]
```

Reads and writes go through the standard {func}`~kandra_runtime.dispatch`; subscriptions go through
{func}`~kandra_runtime.dispatch_subscribe`, which yields a `Result` per pushed frame (in `poll` mode it simply re-issues
the read on the configured interval).

Every operation still returns a {class}`~kandra_runtime.Result` envelope — the library never fails fast on a
non-`ACCEPTED` result; the caller inspects `result.accepted` and reads `result.data` only when accepted. See
[Result](result.md).
