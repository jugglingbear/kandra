# Examples

Kandra ships three example devices under
[`examples/`](https://github.com/jugglingbear/kandra/tree/main/examples), each chosen to illustrate a different slice
of the framework. Pick the one that matches what you're trying to learn.

## At a glance

```{list-table}
:header-rows: 1
:widths: 25 20 55

* - Example
  - Transports
  - Use it to…
* - **hello_world**
  - HTTP
  - See the smallest possible device — one command, one handler. Start here to read a whole manifest in one screen.
* - **pneumatic_bear_poker**
  - HTTP
  - Follow the full lifecycle (discover, enroll, save, connect, dispatch) against a local simulator. This is the
    [Getting Started](getting-started.md) device.
* - **opengopro**
  - BLE + HTTP
  - Study how one command maps onto two very different wire formats: a BLE TLV codec and an HTTP/JSON path.
```

## hello_world — the smallest device

One HTTP command (`greeting.hello`) backed by one handler. Build it and call a single typed method:

```bash
kandra build examples/hello_world/manifest.yaml
```

The generated client exposes `await client.greeting.hello(HelloRequest(message="hello"))`. There is no discovery,
enrollment, or simulator here — it is the minimal shape of a manifest plus a handler, nothing more.

## pneumatic_bear_poker — the full walkthrough

The reference device: HTTP commands, a settable attribute, an event, a discovery probe, and a Flask firmware simulator
you can run locally. It drives the entire credential lifecycle end to end, so it is the device used throughout
[Getting Started](getting-started.md). Reach for it when you want to see how a generated client actually connects to and
talks to a device.

## opengopro — a realistic manifest shape

A dual-transport manifest (BLE **and** HTTP) for a fictional camera: the `shutter.start` / `shutter.stop` commands ride
a BLE TLV codec *and* an HTTP/JSON path, showing how a single logical command is wired to two different wire formats.
This example is **manifest-only** — it demonstrates wiring (custom codecs, per-transport blocks, dual transports) and is
not paired with buildable handler code, so read it alongside [The Manifest](concepts/manifest.md) rather than building
it.
