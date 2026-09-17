# Lifecycle

The end-to-end story of meeting, remembering, and re-connecting to a device. Each phase delegates to one of the six core
abstractions covered in their own pages.

## The Five Phases

```{mermaid}
flowchart LR
    discover([1. Discover<br/>Scanner])
    enroll([2. Enroll<br/>Enrollment])
    save([3. Save<br/>IdentityStore])
    connect([4. Connect<br/>Transport.from_identity])
    dispatch([5. Dispatch<br/>Codec + ResponseInterpreter])

    discover --> enroll --> save --> connect --> dispatch
    dispatch -. reuse .-> connect
    connect -. on stale credentials .-> enroll
```

Phases 1–3 happen **once per device** (first-time pairing). Phases 4–5 happen on **every subsequent session**.

## The One-Call Path (Recommended)

For the common case, the generated client exposes `discover_and_connect()`, which collapses phases 1–4 into a single
call: it tries the store first, and only falls back to scan + enroll
+ save when no record exists for `saved_name`.

```python
from kandra_runtime import BleEnrollment                        # ships with Kandra
from my_device_sdk import MyDeviceClient, http_enrollment       # generated from your manifest
from my_device.handlers.poker import DeployRequest              # your hand-written request model

async with await MyDeviceClient.discover_and_connect(
    saved_name="kitchen",
    # An HTTP-only device needs no enrollment arg at all; a mixed device names each
    # family it must enroll. `http_enrollment()` carries the manifest-baked login path.
    enrollment={
        "ble": BleEnrollment(),
        "http": http_enrollment(),
    },
) as client:
    result = await client.poker.deploy(DeployRequest(pressure_psi=42))
    if result.accepted:
        print(result.data.delivered_psi)
```

Where each import comes from:

- **`kandra_runtime`** — the Kandra library; same for every project. Holds the six protocols, the built-in
  `BleEnrollment` / `HttpEnrollment` adapters, identity types, `Result`, etc.
- **`my_device_sdk`** — *generated* by `kandra build` from your manifest. Holds the `MyDeviceClient` class
  (with `discover_and_connect`, `connect`, and one method per command) and the wiring for transports, codecs,
  and interpreters.
- **`my_device.handlers.poker`** — *your code*. The request/response dataclasses you wrote, referenced by the
  handler class that the manifest's `handler:` field points at. Kandra never touches these — it just imports
  them by dotted path.

- **First run:** scans every discoverable family, enrolls the first
  match, saves a (possibly composite) `Identity`, then opens transports.
- **Every subsequent run:** loads `"kitchen"` from the store and
  opens transports. No BLE scanning, no HTTP login round-trip.

For a single-family device you can pass a bare `Enrollment` instead of a mapping — or, for an HTTP device whose login
is declared in the manifest, omit it entirely:

```python
# HTTP device, login baked from the manifest:
client = await MyDeviceClient.discover_and_connect(saved_name="kitchen")

# BLE-only device (bonding is a runtime step, so still pass the adapter):
client = await MyDeviceClient.discover_and_connect(saved_name="kitchen", enrollment=BleEnrollment())
```

`discover_and_connect()` is only generated when the manifest declares a `discovery:` block; without it, callers must use
the explicit multi-phase flow shown below.

## Full Sequence Diagram

```{mermaid}
sequenceDiagram
    autonumber
    participant App as Your app
    participant Scanner as HttpScanner
    participant Enroll as HttpEnrollment
    participant Store as PlatformDirsJsonStore
    participant Client as MyDeviceClient
    participant Transport as HttpTransport
    participant Device as Device

    rect rgb(240, 248, 255)
    note over App,Device: Phase 1-3: First-time setup (once per device)
    App->>Scanner: scan_http(timeout=5)
    Scanner->>Device: probe /.well-known/<id>
    Device-->>Scanner: 200 OK + Server header
    Scanner-->>App: [Candidate]
    App->>Enroll: enroll(candidate, saved_name="kitchen")
    Enroll->>Device: POST /v1/auth/login
    Device-->>Enroll: {"token": "..."}
    Enroll-->>App: HttpIdentity(auth_token="...")
    App->>Store: save(identity)
    end

    rect rgb(245, 245, 220)
    note over App,Device: Phase 4-5: Every subsequent session
    App->>Client: connect("kitchen")
    Client->>Store: load("kitchen")
    Store-->>Client: HttpIdentity
    Client->>Transport: from_identity(identity)
    Transport->>Device: open() — TCP / TLS
    App->>Client: poker.deploy(req)
    Client->>Transport: request(envelope)
    Transport->>Device: HTTP POST /v1/poker/deploy
    Device-->>Transport: HTTP 200
    Transport-->>Client: HttpResponse
    Client-->>App: Result[DeployResponse]
    end
```

## In Code

`discover_and_connect()` is exactly the orchestration of steps 1–11 above; it doesn't add a new phase — it just hides
the boilerplate.

### When to Use the Explicit Flow

`discover_and_connect()` is the right choice for ~90% of apps. Reach for the explicit phase-by-phase flow when you need
any of:

- **Multiple devices on the bench.** `discover_and_connect()` takes
  the first scan match. To pick between two cameras, call
  `scan_<family>()` directly, choose the candidate, then run
  `Enrollment.enroll()` + `IdentityStore.save()` + `Client.connect()`
  yourself.
- **Custom enrollment flows.** Captive portals, multi-step pairing,
  per-device-class branching, or anything that can't be expressed as
  "call `enroll()` on the first match."
- **Out-of-band identity provisioning.** A manufacturing tool
  pre-populates the store; the app only ever calls `connect()`.
- **Long-running daemons.** Scan + enroll happen at install time in
  a separate setup script; the daemon uses `connect()` only.

The explicit equivalent:

```python
from kandra_runtime import PlatformDirsJsonStore
from my_device_sdk import MyDeviceClient, http_enrollment, scan_http
from my_device.handlers.poker import DeployRequest

# Phase 1: discover
candidates = await scan_http(timeout=5.0)
candidate = candidates[0]  # or a smarter picker

# Phase 2: enroll (login path baked from the manifest)
identity = await http_enrollment().enroll(candidate, saved_name="kitchen")

# Phase 3: save
store = PlatformDirsJsonStore(app_name="my_device_sdk")
store.save(identity)

# Phase 4-5: connect + dispatch (every subsequent run)
async with await MyDeviceClient.connect("kitchen", store=store) as client:
    result = await client.poker.deploy(DeployRequest(pressure_psi=42))
    if result.accepted:
        print(result.data.delivered_psi)
```

## When Phases Are Reused or Re-Run

```{list-table}
:header-rows: 1

* - Trigger
  - Phases to re-run
* - First time ever seeing the device
  - 1 → 2 → 3 → 4 → 5 (one `discover_and_connect()` call)
* - App restart, device unchanged
  - 4 → 5 (the same `discover_and_connect()` call short-circuits to load)
* - Device's IP changed but same identity
  - 4 (transport reconnects automatically) → 5
* - Device factory-reset; bond / token invalid
  - `connect(saved_name, on_stale=re_enroll)` recovers automatically at
    connect time (or run `re_enroll()` then `connect()`) — see below
* - User wants to forget the device
  - `IdentityStore.delete(saved_name)`
```

## Credential Staleness & Re-Enrollment

Stored credentials don't live forever: an HTTP token expires, or a BLE peripheral is factory-reset and forgets its
bond. Kandra surfaces this as a distinct, *recoverable* failure — {class}`~kandra_runtime.IdentityStaleError` (a
subclass of `TransportError`, so existing handlers still catch it) — rather than a generic dropped link.

**Detection.** The HTTP transport maps a `401` / `403` to `IdentityStaleError` by default (opt out with
`HttpTransport(..., stale_statuses=[])`). BLE has no portable "stale bond" signal, so a BLE adapter raises
`IdentityStaleError` itself when it recognizes one and the transport lets it propagate untouched. Because an HTTP
transport opens *lazily* (no request at `open()`), a stale **token** surfaces on the first call, while a stale **BLE
bond** surfaces at `open()` — so connect-time recovery mainly rescues BLE; token expiry is raised to the caller.

**Recovery.** The generated client offers two pieces:

- `re_enroll(saved_name, *, enrollment=...)` — re-discovers the device via the manifest scanners, runs enrollment
  again, and atomically overwrites the saved record. Returns the fresh `Identity`.
- `connect(saved_name, on_stale=...)` — when `open()` raises `IdentityStaleError`, the `on_stale` callback is invoked
  to re-establish credentials and the connect is retried once.

Wire them together so recovery is one argument:

```python
client = await MyDeviceClient.connect(
    "kitchen",
    on_stale=lambda name: MyDeviceClient.re_enroll(name, enrollment=my_enrollment),
)
```

A successful connect also stamps `last_validated` on the stored identity, so a store can surface "credentials last
confirmed working."

### Mid-session auto-refresh

Connect-time recovery rescues a stale *bond* at `open()`, but not a *token that expires mid-session* — because an HTTP
transport opens lazily, that surfaces on a later command. Opt into automatic recovery there with
`refresh_mid_session=True`:

```python
client = await MyDeviceClient.connect(
    "kitchen",
    on_stale=lambda name: MyDeviceClient.re_enroll(name, enrollment=my_enrollment),
    refresh_mid_session=True,
)
```

When enabled, a command whose `dispatch` raises `IdentityStaleError` runs the same `on_stale` recovery, rebuilds the
client's transports in place, re-stamps `last_validated`, and retries that command **exactly once** — a second stale
error propagates.

Crucially it is **off by default** and toggled *independently* of connect-time `on_stale`:

- `on_stale` alone → recover at connect, but let a mid-session `IdentityStaleError` propagate raw.
- `on_stale` + `refresh_mid_session=True` → self-heal in both places.
- neither → every `IdentityStaleError` reaches the caller untouched.

That last stance is deliberate: a **connectivity / auth test harness** wants to *observe* the raw
`IdentityStaleError` (to assert a device really does expire a token or invalidate a bond), so it simply never opts in.
`refresh_mid_session=True` requires `on_stale`; passing it without one raises `ValueError`.

```{note}
Auto-refresh wraps request/response `dispatch` only, not a live `subscribe()` stream (re-establishing a mid-flight
subscription after a transport swap is out of scope). A mid-session BLE re-enroll also tears down and rebuilds the
live connection, so it is heavier than an HTTP token refresh.
```
