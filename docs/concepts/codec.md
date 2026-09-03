# Codec

A **Codec** maps between your user-facing dataclasses and the transport's wire envelopes. It's the only place in the
runtime that *knows* what a given command looks like on the wire.

```{eval-rst}
.. autoclass:: kandra_runtime.codec.Codec
   :members:
   :no-index:
```

## Four Type Parameters

A codec is generic in four types so it can sit precisely between any request/response pair and any transport family:

```{list-table}
:header-rows: 1

* - Parameter
  - Meaning
  - Example
* - `RequestT`
  - User-facing request dataclass
  - `DeployRequest`
* - `ResponseT`
  - User-facing response dataclass
  - `DeployResponse`
* - `WireReqT`
  - Envelope the [`Transport.request()`](transport.md) consumes
  - `HttpRequest`
* - `WireRespT`
  - Envelope the transport produces
  - `HttpResponse`
```

End users rarely write the raw four-param signature. They subclass a **family-paired** base shipped with each transport
— for example `HttpJsonCodec[Req, Resp]` (with `WireReqT` / `WireRespT` already pinned to `HttpRequest` /
`HttpResponse`).

## How It Composes

```{mermaid}
flowchart LR
    user[Your code] -->|DeployRequest| codec[Codec]
    codec -->|HttpRequest envelope| transport[HttpTransport]
    transport -->|HTTP POST| device[Device]
    device -->|HTTP 200 + body| transport
    transport -->|HttpResponse envelope| codec
    codec -->|DeployResponse| user
```

## Family-Paired Bases

```{list-table}
:header-rows: 1

* - Base
  - Transport family
  - You override...
* - `HttpJsonCodec[Req, Resp]`
  - HTTP
  - `path`, `method`, and `_request_body()` / `_parse_response()`.
* - `BleChannelCodec[Req, Resp]`
  - BLE
  - `tx_uuid`, `rx_uuid`, and the encode / decode helpers.
* - `LoopbackCodec[Req, Resp]`
  - Loopback (testing)
  - Just `encode` / `decode` directly on `bytes`.
```

For most commands a manifest entry suffices — the generator produces the codec subclass for you. Hand-written codecs
only appear when a command's wire format isn't expressible declaratively (custom binary framing, content-type
negotiation, etc.).

## Errors

All codec failures derive from a single base:

```{eval-rst}
.. autoclass:: kandra_runtime.errors.CodecError
   :no-index:
```

Encode errors and decode errors share the same exception class because callers almost always handle them identically
(log + classify as `ANOMALOUS`). If you need to disambiguate, attach `extra` context to the exception instance.

## Why Codecs Are Separate From Transports

Two reasons:

1. **One transport, many commands.** A single `HttpTransport` instance
   carries every command for a device. Pushing serialization into a
   per-command codec keeps the transport reusable.
2. **One command, many transports.** The same `DeployRequest` /
   `DeployResponse` pair can have an HTTP codec *and* a BLE codec for
   devices that expose both. The user-facing API stays the same; only
   the wire format differs.

See [Result](result.md) for what happens to the decoded response next.
