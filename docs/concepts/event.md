# Event

An **Event** is a stateless device *emission* — a fire-and-forget notification you can `subscribe` to but never
`read` or `write`. It is the fourth device primitive, alongside commands, attributes, and transports:

- A **command** is a one-shot *action* (`poker.deploy`).
- An **[attribute](attribute.md)** is durable *state* (`settings.poke_intensity`) — read / write / subscribe.
- An **event** is a *stateless emission* (`alerts.bear_stirred`) — subscribe only.

The distinction from an attribute subscribe is deliberate: an attribute subscribe streams *changes to a stored value*
(each update is the new value), whereas an event has no stored value at all — it streams discrete happenings
("button pressed", "recording started", "the bear stirred").

```python
async for stir in client.alerts.bear_stirred.subscribe():
    print(stir.data.magnitude)   # one Result per emission
```

## Handler Contract

An event handler is a plain class declaring a single `payload` type — the shape of each emission. There is no method
body; like a command or attribute handler, it is *data*:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class BearStirred:
    """One "the bear stirred" emission."""

    magnitude: int


class BearStirredAlert:
    """`alerts.bear_stirred` — emitted whenever the bear shifts in its sleep."""

    payload = BearStirred
```

Because a subscribe takes no arguments, the request is the empty {class}`~kandra_runtime.NoArgs` sentinel; you never
construct it yourself. Each emission is delivered as a {class}`~kandra_runtime.Result` wrapping a `payload`.

## Wiring

An event lists the transports it rides and a per-transport subscribe block — the same subscribe wiring an attribute
uses, minus the read/write ops:

```yaml
events:
  - id: alerts.bear_stirred
    handler: devices.pneumatic_bear_poker.handlers.alerts:BearStirredAlert
    transports: [http, ble]
    audience: [internal, partner_woodland]
    http:
      http: { mode: sse, path: /v1/alerts/bear_stirred/events }
    ble:
      ble: { channel: query }
```

Delivery mode is identical to an [attribute subscribe](attribute.md#subscribe-delivery-modes):

```{list-table}
:header-rows: 1

* - Mode
  - Declared as
  - Behaviour
* - `sse`
  - `http: { mode: sse, path: ... }`
  - Native HTTP server-sent events — one long-lived stream, one `Result` per `data:` frame.
* - `poll`
  - `http: { mode: poll, path: ..., interval: 5.0 }`
  - The client re-reads the endpoint every `interval` seconds. **Opt-in only** — `poll` *requires* an `interval`.
* - `ble`
  - `ble: { channel: ... }`
  - The BLE channel's notify characteristic; one `Result` per notification.
```

## Async-Only

`subscribe()` is generated **only on the async client**. Events are inherently a live push stream, and a synchronous
iterator would have to block a thread for the lifetime of the subscription — which defeats the purpose of the sync
facade. As a result, a namespace that contains *only* events is absent from the sync client entirely; a namespace that
mixes commands and events keeps its commands on the sync client and drops the events.

```{list-table}
:header-rows: 1

* - Operation
  - Async facade
  - Sync facade
* - `subscribe`
  - `async for r in client.alerts.bear_stirred.subscribe(): ...`
  - *(not generated — async only)*
```

## Under the Hood

Events reuse the command machinery: each event compiles to a synthetic `<id>.subscribe` command in the generated
registry, carried by {func}`~kandra_runtime.dispatch_subscribe` over the transport's
[`Subscribable`](transport.md) capability (in `poll` mode it re-issues a plain read on the interval instead).

```{mermaid}
flowchart LR
    sub["client.alerts.bear_stirred.subscribe()"] --> disp["dispatch_subscribe"]
    disp --> codec[Codec]
    codec --> transport["Transport (Subscribable)"]
    transport --> device[Device]
    device -. "emission stream" .-> transport
```

Every emission is a {class}`~kandra_runtime.Result` — the library never fails fast; the caller inspects
`result.accepted` and reads `result.data` only when accepted. See [Result](result.md).
