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
from kandra_runtime import BleEnrollment, HttpEnrollment  # ships with Kandra
from my_device_sdk import MyDeviceClient                  # generated from your manifest
from my_device.handlers.poker import DeployRequest        # your hand-written request model

async with await MyDeviceClient.discover_and_connect(
    saved_name="kitchen",
    enrollment={
        "ble": BleEnrollment(),
        "http": HttpEnrollment(login_path="/v1/auth/login"),
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
- **`my_device.handlers.poker`** — *your code*. The request/response Pydantic models you wrote and pointed
  at from the manifest's `request_model:` / `response_model:` fields. Kandra never touches these — it just
  imports them by dotted path.

- **First run:** scans every discoverable family, enrolls the first
  match, saves a (possibly composite) `Identity`, then opens transports.
- **Every subsequent run:** loads `"kitchen"` from the store and
  opens transports. No BLE scanning, no HTTP login round-trip.

For a single-family device (only BLE *or* only HTTP), pass a bare `Enrollment` instead of a mapping:

```python
client = await MyDeviceClient.discover_and_connect(
    saved_name="kitchen",
    enrollment=HttpEnrollment(login_path="/v1/auth/login"),
)
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
from kandra_runtime import HttpEnrollment, PlatformDirsJsonStore
from my_device_sdk import MyDeviceClient, scan_http
from my_device.handlers.poker import DeployRequest

# Phase 1: discover
candidates = await scan_http(timeout=5.0)
candidate = candidates[0]  # or a smarter picker

# Phase 2: enroll
identity = await HttpEnrollment(login_path="/v1/auth/login").enroll(
    candidate, saved_name="kitchen"
)

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
  - 2 → 3 → 4 → 5 — delete the saved record, then call
    `discover_and_connect()` again
* - User wants to forget the device
  - `IdentityStore.delete(saved_name)`
```

```{note}
Automatic re-enrollment on stale credentials (an `IdentityStaleError`
+ `re_enroll()` helper) is planned. Today,
detecting and recovering from staleness is left to the caller.
```
