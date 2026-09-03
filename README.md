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

## Documentation

Full documentation — concepts, architecture and sequence diagrams, API reference, and design notes — lives on the
**[Kandra docs site](https://jugglingbear.github.io/kandra/)**.

- [Why Kandra?](https://jugglingbear.github.io/kandra/motivation.html) — the problem it solves and how a build works.
- [Architecture](https://jugglingbear.github.io/kandra/concepts/architecture.html) — the runtime / generator split.
- [The manifest](https://jugglingbear.github.io/kandra/concepts/manifest.html) — the YAML wiring format.

---

## Quick start

> Kandra is pre-alpha. Today you can author and validate a manifest and run
> `kandra build` to generate a working, typed SDK — client, transports,
> discovery, and enrollment. The audience-pruning and vendoring layer that
> splits one manifest into per-audience packages is still on the way — see
> [Roadmap](#roadmap).

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

A runnable end-to-end example lives in
[`examples/pneumatic_bear_poker/`](examples/pneumatic_bear_poker/). For the manifest format, the architecture, and the
API reference, see the [documentation site](https://jugglingbear.github.io/kandra/).

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
| Built-in BLE transport with named channels (`BleTransport` + per-command `ble.channel`) | ✅ Available |
| Discovery, enrollment, identity persistence (`Scanner` / `Enrollment` / `IdentityStore` + `connect()` / `discover_and_connect()`) | ✅ Available |
| Audience pruning, leakage scan, vendoring (`internal` vs `partner-*` artifacts from one manifest) | ⏸️ Planned |
| Capability negotiation + `Attribute` (read / write / subscribe) + `Event` primitives | ⏸️ Planned |
| Credential lifecycle — re-enrollment / rotation (`IdentityStaleError`, `re_enroll()`) | ⏸️ Planned |

---

## Status

Pre-alpha. The runtime API and manifest schema are likely to change without warning. Don't depend on Kandra in
production yet.
