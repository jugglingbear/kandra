# Getting Started

A hands-on, no-hardware walkthrough: you'll **build** the Pneumatic Bear Poker SDK, run a **local device simulator**,
and drive the device through its full lifecycle — discover, enroll, save, connect, and send a command.

The [Pneumatic Bear Poker](concepts/manifest.md) is Kandra's fictional reference device: a gadget whose sole purpose is
to poke bears, pneumatically. Everything below runs against a Flask simulator, so no real hardware (or bear) is
required.

## Prerequisites

- **Python 3.11+** and **[Poetry](https://python-poetry.org)**.
- That's all — the local simulator uses Flask, which `make install` provides. Docker is optional; see
  [Run the simulator in Docker](#run-the-simulator-in-docker).

## 1. Install

```bash
make install
```

This creates a shared `.venv` with both `kandra` (the generator + CLI) and `kandra_runtime` (the SDK runtime) installed
editable.

## 2. Build the example SDK

```bash
make build-example
```

Under the hood this runs `kandra build examples/pneumatic_bear_poker/manifest.yaml` and writes a self-contained, typed
client package to `dist/pneumatic_bear_poker_sdk/`:

```text
dist/pneumatic_bear_poker_sdk/
├── __init__.py       # re-exports PneumaticBearPokerClient, scan_http, ...
├── client.py         # typed client facade: connect() / discover_and_connect() + one method per command
├── registry.py       # per-command codec + interpreter wiring
├── transports.py     # transport factory table
├── scanners.py       # discovery helpers baked from the manifest's discovery block
└── py.typed          # PEP 561 marker
```

## 3. Start the device simulator

In one terminal, start the Flask firmware simulator. It implements every HTTP endpoint in the manifest — the commands,
the discovery probe, and the enrollment login:

```bash
poetry run python -m examples.pneumatic_bear_poker.firmware_sim.app
```

It serves on `http://localhost:8080`. Leave it running.

```{note}
**Port 8080 already in use?** Plenty of tools (VS Code among them) squat on 8080. Start the sim on another port with
`PORT=8081 poetry run python -m examples.pneumatic_bear_poker.firmware_sim.app`, then pass the demo the matching
`--url http://localhost:8081` in step 4. The two must agree -- discovery probes exactly the URL you give it.
```

## 4. Run the lifecycle demo

In a **second** terminal, run the demo. It needs the generated SDK (`dist/`) and the example's handler source
(`examples/pneumatic_bear_poker/src/`) on `PYTHONPATH`:

```bash
PYTHONPATH="dist:examples/pneumatic_bear_poker/src" \
  poetry run python examples/pneumatic_bear_poker/demo.py
```

(If your sim is on a non-default port, add `--url http://localhost:<port>`.) You should see each lifecycle phase
logged, ending in a successful deploy:

```text
[STORE] identities file: ~/Library/Application Support/pneumatic_bear_poker_sdk/identities.json
[STORE] currently saved: (none)
[PHASE] no saved identity 'grizzly' -- running discover + enroll
[DISCOVER] probing http://localhost:8080 for a Pneumatic Bear Poker ...
[DISCOVER] found device at http://localhost:8080
[ENROLL] POSTing /v1/auth/login and capturing the bearer token ...
[ENROLL] credentials captured
[SAVE] identity 'grizzly' written to ~/Library/Application Support/pneumatic_bear_poker_sdk/identities.json
[CONNECT] reopening device from saved identity 'grizzly' ...
[DEPLOY] sending poker.deploy(pressure_psi=42) ...
[DEPLOY] accepted: bear poked, delivered_psi=42
```

That last line is the round trip: your Python `DeployRequest(pressure_psi=42)` went out over HTTP, and the device's
`DeployResponse(delivered_psi=42)` came back — decoded and wrapped in a `Result`.

## What just happened

[`demo.py`](https://github.com/jugglingbear/kandra/blob/main/examples/pneumatic_bear_poker/demo.py)
walks the phases every real caller follows — each is a single line in the generated SDK:

1. **Discover** — `scan_http()` probes the device's `/.well-known/...` endpoint and matches on the manifest's
   [discovery](concepts/scanner.md) criteria. (The demo passes `base_urls=[...]` to aim it at the local sim.)
2. **Enroll** — `HttpEnrollment` POSTs the login form and captures a bearer token, producing a persistent
   [Identity](concepts/identity.md).
3. **Save** — `PlatformDirsJsonStore` writes that identity to a per-user data dir (printed as the `[STORE]` line
   above), so next time you skip discovery and enrollment entirely.
4. **Connect** — `PneumaticBearPokerClient.connect("grizzly")` reloads the saved identity, opens the transport,
   and returns an async context manager.
5. **Dispatch** — `await client.poker.deploy(...)` encodes the request, sends it, classifies the response, and returns
   a typed [`Result`](concepts/result.md).

Run the demo a second time and it skips straight to step 4 — the saved identity short-circuits discovery and
enrollment. See [Lifecycle](concepts/lifecycle.md) for the full picture (and the one-call `discover_and_connect()`
shortcut that collapses steps 1–4).

## Clearing a saved identity

That saved identity is a small JSON cache — the demo prints its exact location each run (the `[STORE]` line). It
pins the address captured at enrollment, so if the device later moves (a new sim port or IP), reconnecting to a
stale address fails with `TRANSPORT_FAILURE` — the demo detects that and prints a hint. To forget it and re-enroll:

```bash
PYTHONPATH="dist:examples/pneumatic_bear_poker/src" \
  poetry run python examples/pneumatic_bear_poker/demo.py --reset --url http://localhost:8080
```

`--reset` forgets the `grizzly` identity before running; deleting the JSON file shown in the `[STORE]` line does the
same by hand.

```{note}
Kandra *does* auto-detect one kind of staleness: expired or revoked **credentials** (an HTTP `401`/`403`) surface as a
recoverable [`IdentityStaleError`](concepts/lifecycle.md) that triggers re-enrollment. A moved **address**, though, is
indistinguishable from a device that is simply offline, so it cannot be auto-detected — hence `--reset`.
```

## Point it at real hardware

The demo defaults to the local sim. To drive a real device, point `--url` at it:

```bash
PYTHONPATH="dist:examples/pneumatic_bear_poker/src" \
  poetry run python examples/pneumatic_bear_poker/demo.py --url http://192.168.1.50:8080
```

## Run the simulator in Docker

Prefer a container? The simulator ships a Dockerfile:

```bash
docker build -t pneumatic-bear-poker-sim examples/pneumatic_bear_poker/firmware_sim
docker run --rm -p 8080:8080 pneumatic-bear-poker-sim
```

Then run the demo exactly as in step 4.

## Next steps

- Read the [manifest](concepts/manifest.md) you just built from — commands, attributes, events, and discovery are all
  declared there.
- Skim the [Architecture](concepts/architecture.md) and [Lifecycle](concepts/lifecycle.md) concept pages.
- Explore the rest of the generated client: `client.safety.emergency_retract()`, the
  `client.settings.poke_intensity` [attribute](concepts/attribute.md) (read / write / subscribe), and the
  `client.alerts.bear_stirred` [event](concepts/event.md).
- Build a **pruned, per-audience** SDK with `kandra build --profile partner_woodland` — see
  [Audiences & IP Isolation](concepts/audiences.md).
```

