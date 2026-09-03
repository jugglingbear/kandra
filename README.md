# Kandra

> **Kandra**, n.: in Brandon Sanderson's *Mistborn*, a shapeshifter that
> consumes the bones of a creature and perfectly takes on its form. Here:
> a framework that consumes a device manifest and produces a working
> Python SDK in that device's shape.

[![status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange)](#status) [![python:
3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

Kandra generates **typed, lint-clean, IP-isolated Python SDKs** for embedded-Linux devices that speak over multiple
transports — BLE, HTTP over Wi-Fi or USB-CDC, raw sockets, serial, you name it. You describe a device once, in Python
plus a small YAML manifest, and Kandra produces a stand-alone client package for each audience (internal, partner,
public, …) containing only the code that audience is allowed to see.

---

## Table of contents

- [Why does this exist?](#why-does-this-exist)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [The manifest](#the-manifest)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Development](#development)
- [Roadmap](#roadmap)
- [Status](#status)

---

## Why does this exist?

If you ship an embedded device, you probably ship an SDK with it. If you ship more than one device, or sell to more than
one partner, you quickly end up with one of the following:

- **One giant SDK** that every customer sees, with IP leakage and a
  bloated API surface.
- **N hand-maintained forks**, one per device or per customer, that
  drift apart and rot.
- **A code generator over an IDL** (protobuf, OpenAPI, …) that forces
  you to duplicate every type definition in a schema language and gives
  you generated code your engineers don't want to read.

Kandra picks a different tradeoff:

- **Python is the source of truth.** Models, handlers, transports, and
  codecs are normal Python written in your repo. You refactor with the
  IDE, lint with ruff, and test with pytest. No schema language.
- **YAML is wiring only.** The manifest references your Python classes
  by dotted path and declares which commands ship on which transports
  for which audience. It contains **no** type definitions.
- **Audience is first-class.** Each command, attribute, and module is
  tagged. The generator emits one SDK per audience, and a final
  subprocess import-check fails the build if anything leaks across.
- **One runtime, many SDKs.** The generated package depends on a small
  `kandra-runtime` PyPI package and otherwise vendors only its own
  transitive code closure.

---

## How it works

```text
   authoring inputs                 build step                  per-audience artifact
  ┌──────────────────┐            ┌─────────────┐            ┌───────────────────────┐
  │ device.yaml      │            │             │            │ woodland_sdk/         │
  │ handlers/*.py    │  ───────▶  │   kandra    │  ───────▶  │   client.py           │
  │ transports/*.py  │            │   build     │            │   commands/*.py       │
  │ codecs/*.py      │            │             │            │   models/*.py         │
  │ models/*.py      │            └─────────────┘            │   registry.py         │
  └──────────────────┘                                       │   _generated_from.json│
                                                             └───────────────────────┘
                                                                       │
                                                                       ▼
                                                          ┌──────────────────────────┐
                                                          │   kandra-runtime (pip)   │
                                                          │   Transport, Codec,      │
                                                          │   Command, dispatcher    │
                                                          └──────────────────────────┘
```

For each manifest the generator:

1. Loads the YAML into a pydantic model.
2. Imports the handler/transport/codec classes named in the manifest.
3. Walks their import closure, restricted to the `source_roots` listed
   in the manifest (never stdlib, third-party, or unlisted packages).
4. Audience-filters the closure.
5. Vendors the surviving files into `dist/<audience>/<sdk_pkg>/`.
6. Emits a thin typed facade so consumers write
   `await client.poker.deploy(...)` instead of `dispatch(cmd, …)`.
7. Verifies the result imports cleanly in a clean subprocess — if a
   dynamic import reaches back into the source tree, the build fails.

---

## Quick start

> Kandra is in early development. Today you can author and validate a
> manifest and drive the runtime from Python directly. The code
> generator that turns a manifest into a per-audience SDK package is
> still on the way — see [Roadmap](#roadmap).

```bash
git clone <this-repo> your_project
cd your_project
make install         # creates .venv via Poetry
make qa              # ruff + mypy + pytest
make validate        # validates examples/pneumatic_bear_poker/manifest.yaml
```

Validating a manifest yourself:

```bash
poetry run kandra validate path/to/manifest.yaml
```

Emitting the manifest JSON Schema (for editor autocomplete):

```bash
poetry run kandra schema > manifest.schema.json
```

Driving the runtime from Python (a tiny stand-in for what the generated SDK will do for you once code generation lands):

```python
import asyncio
from dataclasses import dataclass
from kandra_runtime import (
    Command, LoopbackTransport, dispatch, open_transport,
)


@dataclass(frozen=True)
class PokeRequest:
    pressure_psi: int


@dataclass(frozen=True)
class PokeResponse:
    delivered_psi: int


class PokeCodec:
    def encode(self, request: PokeRequest) -> bytes:
        return str(request.pressure_psi).encode("ascii")

    def decode(self, payload: bytes) -> PokeResponse:
        return PokeResponse(delivered_psi=int(payload))


async def fake_bear_poker(payload: bytes) -> bytes:
    # The real device would actuate a piston; here we just bump the
    # requested pressure by 1 PSI to prove the round-trip ran.
    return str(int(payload) + 1).encode("ascii")


async def main() -> None:
    poke = Command(id="poker.deploy", codec=PokeCodec(), timeout=1.0)
    transport = LoopbackTransport(fake_bear_poker)
    async with open_transport(transport):
        result = await dispatch(poke, transport, PokeRequest(pressure_psi=41))
    print(result)  # PokeResponse(delivered_psi=42)


asyncio.run(main())
```

---

## The manifest

A manifest describes **one device**. A repo can hold any number of manifests side-by-side — typically one per device
under a shared `src/devices/<device_id>/` tree plus `src/common/` for things like codecs and transports that are reused
across devices. The generator is invoked once per (manifest, audience) pair and produces a distinct SDK package each
time, so N devices × M audiences = N × M shipped SDKs out of a single source repo.

Kandra ships with one reference manifest: the **Pneumatic Bear Poker**, a fictional device whose sole job is to poke
bears (pneumatically). It has one operational command (`poker.deploy`) and one safety command
(`safety.emergency_retract`), exposed over BLE and HTTP to two audiences: the internal team and the `partner_woodland`
partner.

A minimal manifest looks like this ([full example](examples/pneumatic_bear_poker/manifest.yaml)):

```yaml
schema_version: 1

device:
  id: pneumatic_bear_poker
  display_name: Pneumatic Bear Poker
  firmware_min: "2.4.0"
  audience: [internal, partner_woodland]

source_roots:
  - src/devices/pneumatic_bear_poker
  - src/common

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
    audience: [internal, partner_woodland, public]
    timeout: 5.0
    idempotent: false

  - id: safety.emergency_retract
    handler: null              # uses the default echo-and-decode handler
    transports: [http, ble]
    audience: [internal, partner_woodland, public]
    timeout: 1.0
    idempotent: true

vendoring:
  extra_include: []
  exclude: []
```

What the manifest does **not** contain: request/response field definitions, type schemas, business logic, or anything
else that would duplicate Python. Those live in the handler classes referenced by `handler:`.

---

## Architecture

Kandra has a strict three-layer split:

| Layer | What it is | Who ships it |
|---|---|---|
| **Authoring inputs** | Your Python (handlers, models, transports, codecs) plus per-device YAML manifests. | Lives only in your repo — never shipped. |
| **Runtime** (`kandra-runtime`) | `Transport`, `Codec`, `Command` protocols; dispatcher; error model; loopback transport for tests. | Public PyPI package, audience-agnostic. |
| **Generated SDK** | One Python package per audience: typed facade, vendored handlers/models, capability registry. Depends only on `kandra-runtime` plus its own vendored closure. | Built per release, shipped to that audience. |

See [`kandra.md`](kandra.md) for full design notes and the current decision log.

---

## Project layout

```text
kandra/
├── pyproject.toml
├── Makefile
├── kandra.md                       ← design notes (living doc)
├── README.md                       ← you are here
├── src/
│   ├── kandra/                     ← the generator + CLI (not shipped to clients)
│   │   ├── cli.py                  ← `kandra schema` / `kandra validate`
│   │   ├── loader.py               ← YAML → pydantic Manifest
│   │   └── manifest/               ← pydantic models for the manifest
│   └── kandra_runtime/             ← public PyPI runtime (shipped to clients)
│       ├── transport.py            ← Transport protocol + open_transport()
│       ├── codec.py                ← Codec protocol
│       ├── command.py              ← Command + dispatch / dispatch_sync
│       ├── loopback.py             ← LoopbackTransport (tests / examples)
│       └── errors.py               ← KandraError hierarchy
├── examples/
│   └── pneumatic_bear_poker/       ← reference manifest used by the test suite
└── tests/
```

---

## Development

Requires Python 3.11+ and [Poetry](https://python-poetry.org).

```bash
make help        # list available targets
make install     # install dependencies into .venv
make qa          # ruff + mypy + pytest (zero warnings, zero errors)
make test        # pytest only
make lint        # ruff only
make typecheck   # mypy --strict only
make validate    # validate the example manifest
make schema      # print the manifest JSON Schema
make clean       # remove build artifacts and caches
make reinstall   # wipe .venv and start over
```

All code must pass `make qa` (ruff clean, mypy strict clean, 100% of tests passing) before being committed.

---

## Roadmap

| Capability | Status |
|---|---|
| Manifest model, YAML loader, JSON Schema export | ✅ Available |
| Runtime protocols (`Transport`, `Codec`, `Command`, `ResponseInterpreter`), loopback transport, async + sync dispatch | ✅ Available |
| Code generator: emit typed command stubs, models, async + sync client facades from a manifest | ✅ Available |
| Generic `Transport[WireReqT, WireRespT]` envelope + per-transport `expects_response` (`SyncClient` + fire-and-forget) | ✅ Available |
| Built-in async HTTP transport (`HttpTransport` over `aiohttp`) with GET / POST / PUT / DELETE | ✅ Available |
| Typed `Result[T]` envelope with `ACCEPTED` / `REJECTED` / `DEVICE_FAULT` / `TRANSPORT_FAILURE` / `ANOMALOUS` classification, pluggable `ResponseInterpreter`, `on_non_accepted` hook, `ignore_failures()` context manager | ✅ Available |
| Built-in BLE transport with named channels (`BleTransport` + per-command `ble.channel`) | ⏸️ Planned |
| Discovery, enrollment, identity persistence (`Scanner` / `Enrollment` / `IdentityStore` protocols + `discover()`) | ⏸️ Planned |
| Audience pruning, leakage scan, vendoring (`internal` vs `partner-*` artifacts from one manifest) | ⏸️ Planned |
| Capability negotiation + `Attribute` (read / write / subscribe) + `Event` primitives | ⏸️ Planned |

---

## Status

Pre-alpha. The runtime API and manifest schema are likely to change without warning. Don't depend on Kandra in
production yet.
