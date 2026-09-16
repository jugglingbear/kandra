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

End users rarely touch all four parameters. HTTP/JSON commands need no codec at all — the generator wires the runtime's
`HttpJsonCodec` from your `http:` block. For any other wire format you write a **payload codec**: a plain
`Codec[Req, Resp, bytes, bytes]` that turns your dataclasses into `bytes` and back, which the generator adapts to the
transport family (see below).

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

## What you write per family

For most commands you write no codec at all — the generator instantiates the runtime codecs below from the manifest.
What you *may* write is a **payload codec**: a plain `Codec[Req, Resp, bytes, bytes]` for BLE or custom families, or a
per-command `codec:` override for a one-off wire format (see below).

```{list-table}
:header-rows: 1

* - Family
  - Runtime codec (generator-instantiated)
  - What you write
* - HTTP
  - `HttpJsonCodec`, built from the `http:` block (`method` / `path`) and your handler's types
  - Nothing — omit `codec:` and use plain dataclasses
* - BLE
  - `BleChannelCodec`, which wraps your payload codec with the manifest's `ble.channel`
  - A payload codec `Codec[Req, Resp, bytes, bytes]` (dataclass ↔ `bytes`)
* - Loopback / custom
  - the transport calls your codec directly
  - A codec over `bytes`: `Codec[Req, Resp, bytes, bytes]`
```

For most commands a manifest entry suffices. A hand-written payload codec only appears when a command's wire format
isn't plain JSON — custom binary framing, TLV, protobuf, and the like.

## Per-command codec override

The table above is the default: every command on a transport shares one codec — HTTP's `HttpJsonCodec`, or the BLE
transport's payload codec. When a *single* transport must carry more than one wire format, a command's per-transport
wire block may name its own `codec:`, a dotted `module:Class` reference into your source tree:

```yaml
commands:
  - id: media.download
    handler: devices.cam.handlers.media:Download
    transports: [http]
    http:
      http:
        method: GET
        path: /videos/{id}
        codec: devices.cam.codecs:RawBodyCodec   # returns raw bytes, not JSON
```

- **HTTP.** The override replaces the built-in `HttpJsonCodec` for that one command. It is constructed with the same
  arguments (`method`, `path`, request/response types, `query_from_request`), so the usual pattern is to subclass
  `HttpJsonCodec` and override `decode` (e.g. to return raw bytes or parse a non-JSON body). Because a codec builds
  the whole `HttpRequest`, header quirks live here too.

- **BLE.** The override replaces the transport's payload codec for that command's channel — a plain
  `Codec[Req, Resp, bytes, bytes]` constructed with `(request_type, response_type)` and wrapped in `BleChannelCodec`.

The override module is vendored into the SDK like any handler, so it must live under a `source_roots` tree. This is
what lets one HTTP transport serve JSON status *and* a binary media download, or one BLE link carry a TLV control
channel *and* an opaque-payload channel.

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
