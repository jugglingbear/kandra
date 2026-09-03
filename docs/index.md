# Kandra

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
from kandra_runtime import HttpEnrollment
from my_device_sdk import MyDeviceClient
from my_device.handlers.poker import DeployRequest

async with await MyDeviceClient.discover_and_connect(
    saved_name="kitchen",
    enrollment=HttpEnrollment(login_path="/v1/auth/login"),
) as client:
    result = await client.poker.deploy(DeployRequest(pressure_psi=42))
    if result.accepted:
        print(result.data.delivered_psi)
```

First run scans, enrolls, and saves an `Identity`; every later run short-circuits to a plain `connect()`. See
[Lifecycle](concepts/lifecycle.md) for the full picture and the explicit phase-by-phase flow.

```{toctree}
:caption: Concepts
:maxdepth: 2

concepts/architecture
concepts/lifecycle
concepts/identity
concepts/scanner
concepts/enrollment
concepts/transport
concepts/codec
concepts/result
```

```{toctree}
:caption: Reference
:maxdepth: 1

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
