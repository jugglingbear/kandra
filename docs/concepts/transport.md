# Transport

A **Transport** is a bidirectional channel to a device, typed on the *wire envelope* it carries. It is deliberately
ignorant of message semantics — semantics live one layer up in [Codec](codec.md).

```{eval-rst}
.. autoclass:: kandra_runtime.transport.Transport
   :members:
   :no-index:
```

## Typed Envelopes

The protocol's two type parameters fix the wire-envelope types for a given transport family:

```{list-table}
:header-rows: 1

* - Transport
  - `WireReqT`
  - `WireRespT`
* - HTTP
  - `HttpRequest`
  - `HttpResponse`
* - BLE
  - `BleRequest`
  - `bytes`
* - Loopback (testing)
  - `bytes`
  - `bytes`
```

The user-facing dataclasses (`DeployRequest`, …) are converted to and from these envelopes by the [Codec](codec.md). The
transport itself only sees opaque envelopes, which is why one transport works for many commands.

## Lifecycle

```{mermaid}
stateDiagram-v2
    [*] --> Constructed
    Constructed --> Open: await open()
    Open --> Open: await request(env)
    Open --> Closed: await close()
    Closed --> Open: await open()
    Closed --> [*]
```

The `is_open` property reports the current state. Implementations must tolerate `open()` and `close()` being called more
than once — the {func}`~kandra_runtime.transport.open_transport` async context manager relies on this.

## Construction From an Identity

Every built-in transport exposes a `from_identity()` class method that hydrates a transport from a saved
[Identity](identity.md):

```python
transport = HttpTransport.from_identity(identity)   # HttpIdentity
async with open_transport(transport) as t:
    response = await t.request(envelope)
```

For [`CompositeIdentity`](identity.md#compositeidentity-when-one-device-needs-more-than-one-transport), the generator
picks each sub-identity by key and wires up the matching transport.

## Single-Request Lifecycle

```{mermaid}
sequenceDiagram
    autonumber
    participant Caller
    participant T as Transport
    participant Net as Network / BLE link
    participant Device

    Caller->>T: await open()
    T->>Net: Establish connection<br/>(TCP / TLS / GATT)
    Net->>Device: Handshake
    Device-->>T: is_open = True

    Caller->>T: await request(envelope)
    T->>Device: Send wire bytes
    Device-->>T: Reply bytes
    T-->>Caller: WireRespT

    Caller->>T: await close()
    T->>Net: Tear down connection
```

## Error Contract

All transport-level failures derive from a single base class so calling code only needs one `except`:

```{eval-rst}
.. autoclass:: kandra_runtime.errors.TransportError
   :no-index:
.. autoclass:: kandra_runtime.errors.TransportNotOpenError
   :no-index:
.. autoclass:: kandra_runtime.errors.TransportTimeoutError
   :no-index:
```

`TransportTimeoutError` also subclasses the built-in `TimeoutError` so existing timeout handlers keep working.

## Streaming / Notifications

The core `Transport` protocol is **request/response only** — one envelope in, one envelope out. Push delivery is an
*opt-in* capability expressed by a separate protocol, {class}`~kandra_runtime.transport.Subscribable`:

```{eval-rst}
.. autoclass:: kandra_runtime.transport.Subscribable
   :members:
   :no-index:
```

A transport that can carry server-initiated updates (HTTP server-sent events, a BLE notify characteristic, or a scripted
loopback) implements `subscribe(envelope)` and returns an `AsyncIterator` of wire responses. Keeping it out of the base
`Transport` means a plain request/response transport is never forced to fake streaming, and consumers can
`isinstance(transport, Subscribable)` to negotiate at runtime.

`subscribe()` powers the [Attribute](attribute.md) `.subscribe()` facade (and, in a later milestone, events). The built-in
transports wire it up as follows:

```{list-table}
:header-rows: 1

* - Transport
  - `subscribe()` mechanism
* - HTTP
  - Server-sent events (`text/event-stream`); one `HttpResponse` yielded per `data:` frame.
* - BLE
  - Drains the channel's notify queue; one `bytes` payload yielded per notification.
* - Loopback (testing)
  - Replays a scripted `subscribe_handler` — deterministic push for unit tests.
```

Bulk **byte downloads** (`stream()`) remain deferred until a feature consumes them — see `kandra.md` §8 for the roadmap
and §11.5 Q6 for the rationale.

