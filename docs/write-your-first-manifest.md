# Write Your First Manifest

This tutorial writes a manifest from scratch for the **Bear Feeder**, a made-up Wi-Fi gadget that drops honey into a
bowl. It has one action (dispense honey) and one value you can read (how full the honey tank is). At the end you'll
have a typed Python SDK and a fake device to try it on.

Each step links to the page for the key it adds, where every field is listed.

```{tip}
Prefer to start from a template? `kandra create-sdk my-device` asks a few questions and creates a new Poetry project
with a starter manifest and handler.
```

## Before You Start

Run `make install` in the Kandra repo (see [Getting Started](getting-started.md)), then activate its virtual
environment so `kandra` is on your path:

```bash
source .venv/bin/activate
```

Make a folder for the device anywhere you like, and run every command below from inside it. When you're done it will
look like this:

```text
bear_feeder/
├── manifest.yaml
└── src/
    └── bear_feeder/
        ├── __init__.py      # empty
        └── handlers.py
```

## 1. Name the Device

Create `manifest.yaml` with the three keys every manifest starts with:

```yaml
schema_version: 1

device:
  id: bear_feeder
  display_name: Bear Feeder
  audience: [public]

source_roots:
  - src
```

- [`schema_version`](concepts/manifest/schema_version.md) is always `1`.
- [`device`](concepts/manifest/device.md) names the device. The `id` also names the SDK: you'll get a
  `bear_feeder_sdk` package with a `BearFeederClient` class. `audience` says who gets an SDK; `public` is fine for now.
- [`source_roots`](concepts/manifest/source_roots.md) says where your Python classes live, relative to the manifest.

## 2. Say How to Reach It

The feeder speaks HTTP over Wi-Fi. Add one [transport](concepts/manifest/transports.md):

```yaml
transports:
  - id: wifi
    family: http
```

The `id` is your name for this connection; commands refer to it. HTTP sends JSON by default, so there's no codec to
write.

## 3. Add a Command

A command has two halves: a Python handler class that names the request and reply types, and a manifest entry that
says where to send it.

Create `src/bear_feeder/handlers.py`, plus an empty `src/bear_feeder/__init__.py` next to it:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class DispenseRequest:
    grams: int


@dataclass(frozen=True)
class DispenseResponse:
    dispensed_grams: int


class Dispense:
    """Drop some honey into the bowl."""

    request = DispenseRequest
    response = DispenseResponse
```

Then add the command to the manifest:

```yaml
commands:
  - id: feeder.dispense
    handler: bear_feeder.handlers:Dispense
    transports: [wifi]
    audience: [public]
    http:
      wifi: { method: POST, path: /dispense }
```

- `id` becomes the method name: `feeder.dispense` gives `client.feeder.dispense(...)`. It needs the dot.
- `handler` points at the class, as `module:Class`.
- `http.wifi` says what to send on the `wifi` transport: a `POST` to `/dispense` with the `DispenseRequest` as the
  JSON body. The device's JSON reply comes back as a `DispenseResponse`.

See [commands](concepts/manifest/commands.md) for timeouts, retries, and the other fields.

## 4. Add an Attribute

The honey level is something the device keeps, not something it does, so it's an
[attribute](concepts/manifest/attributes.md). Its handler names one `value` type instead of a request and reply. Add
this to `handlers.py`:

```python
@dataclass(frozen=True)
class Level:
    percent: int


class HoneyLevel:
    """How full the honey tank is."""

    value = Level
```

And this to the manifest:

```yaml
attributes:
  - id: feeder.honey_level
    handler: bear_feeder.handlers:HoneyLevel
    transports: [wifi]
    operations: [read]
    audience: [public]
    http:
      wifi:
        read: { method: GET, path: /honey_level }
```

`operations: [read]` makes it read-only: the SDK gets `client.feeder.honey_level.read()` and no `write()`.

## 5. Validate and Build

Check the manifest:

```bash
kandra validate manifest.yaml
```

```text
OK: manifest.yaml — device=bear_feeder transports=1 commands=1 attributes=1
```

If something is wrong, the error names the exact spot. Misspell `method` as `metod`, for example, and you get:

```text
manifest.yaml: manifest validation failed (2 error(s)):
  - commands.0.http.wifi.method: Field required
  - commands.0.http.wifi.metod: Extra inputs are not permitted
```

Then build the SDK:

```bash
kandra build manifest.yaml
```

It's written to `dist/bear_feeder_sdk/`.

## 6. Try It

Save this fake feeder as `fake_feeder.py` and start it:

```python
from flask import Flask, jsonify, request

app = Flask(__name__)
tank = {"percent": 80}


@app.post("/dispense")
def dispense():
    grams = request.get_json()["grams"]
    tank["percent"] -= grams // 10
    return jsonify({"dispensed_grams": grams})


@app.get("/honey_level")
def honey_level():
    return jsonify(tank)


app.run(port=8080)
```

```bash
python fake_feeder.py
```

In a second terminal (with the virtual environment active), save this as `try_it.py`:

```python
import asyncio

from bear_feeder.handlers import DispenseRequest
from bear_feeder_sdk import BearFeederClient, TransportId
from kandra_runtime import HttpTransport, open_transport


async def main() -> None:
    transport = HttpTransport("http://127.0.0.1:8080")
    async with open_transport(transport):
        client = BearFeederClient(transports={TransportId.WIFI: transport})
        dispensed = await client.feeder.dispense(DispenseRequest(grams=50))
        level = await client.feeder.honey_level.read()
    print(dispensed.data)
    print(level.data)


asyncio.run(main())
```

Run it with the SDK (`dist`) and your handlers (`src`) on the import path:

```bash
PYTHONPATH="dist:src" python try_it.py
```

```text
DispenseResponse(dispensed_grams=50)
Level(percent=75)
```

The script points the client straight at a URL, which is handy for a quick test. Real apps let the SDK find the device
and log in; see [Lifecycle](concepts/lifecycle.md).

## Next Steps

- Let the SDK find the device on the network: add [`discovery`](concepts/manifest/discovery.md).
- Log in to the device: add [`enrollment`](concepts/manifest/transports.md#enrollment-http) to the transport.
- Get told when something happens: add [`events`](concepts/manifest/events.md), or `subscribe` to an attribute.
- Ship different SDKs to different customers: see [Audiences & IP Isolation](concepts/audiences.md).
- Get autocomplete while you edit the manifest: see [Editor Autocomplete](reference/editor-autocomplete.md).
- See a bigger device in action: [Getting Started](getting-started.md) runs the Pneumatic Bear Poker.
