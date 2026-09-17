# Kandra

```{image} _static/kandra.png
:alt: You provide the bones (your device-specific code); Kandra brings your SDK to life.
:width: 100%
```

**Kandra** is a framework for generating typed, IP-isolated Python SDKs that control embedded-Linux devices over BLE,
HTTP, and other transports from a single declarative manifest.

You bring:

- A **manifest** (`manifest.yaml`) describing the device's transports,
  commands, codecs, and discovery rules.
- Plain Python **request/response dataclasses** for each command.

Kandra generates:

- A typed **client class** with `MyDeviceClient.discover_and_connect()`
  for one-call setup-and-go, plus `connect()` / `list_saved()` for the
  explicit lifecycle.
- **Discovery helpers** (`scan_ble()`, `scan_http()`) tuned to your device.
- **Codec wiring** and a **classification pipeline** that turns wire bytes
  into a uniform `Result[ResponseT]` envelope.

## Quickstart

```python
from my_device_sdk import MyDeviceClient               # From your generated SDK
from my_device.handlers.some_group import SomeRequest  # From your handler code

# The HTTP login endpoint is declared in the manifest, so the common case needs no enrollment adapter.
async with await MyDeviceClient.discover_and_connect(saved_name="kitchen") as client:
    result = await client.some_group.some_command(SomeRequest(...))
    if result.accepted:
        print(result.data)
```

First run scans, enrolls, and saves an `Identity`; every later run short-circuits to a plain `connect()`. See
[Lifecycle](concepts/lifecycle.md) for the full picture and the explicit phase-by-phase flow.

```{toctree}
:caption: Overview
:maxdepth: 1

motivation
getting-started
examples
design-review
```

```{toctree}
:caption: Concepts
:maxdepth: 2

concepts/architecture
concepts/manifest
concepts/lifecycle
concepts/identity
concepts/scanner
concepts/enrollment
concepts/transport
concepts/codec
concepts/attribute
concepts/event
concepts/capabilities
concepts/result
concepts/audiences
```

```{toctree}
:caption: Reference
:maxdepth: 1

reference/manifest
reference/runtime
reference/generator
```

## At a Glance

```{mermaid}
flowchart LR
    manifest[manifest.yaml] --> gen([kandra build])
    handlers[Request / Response<br/>dataclasses] --> gen
    gen --> sdk[Generated SDK package]
    sdk --> client[MyDeviceClient]
    client -. uses .-> runtime[(kandra_runtime)]
    runtime --> ble[(BLE)]
    runtime --> http[(HTTP)]
```

## Where to Start

- **New to the project?** Read [Architecture](concepts/architecture.md) and
  then [Lifecycle](concepts/lifecycle.md).
- **Trying to understand one piece?** Jump straight to its concept page
  ([Identity](concepts/identity.md), [Scanner](concepts/scanner.md), etc.).
- **Looking up an API?** Reference pages render docstrings + Pydantic field
  tables straight from source.
