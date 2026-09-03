# Enrollment

**Enrollment** is the one-time handshake that turns an in-range [`Candidate`](scanner.md#the-candidate-record) into a
persistent [`Identity`](identity.md). Concretely: BLE bonding for BLE devices, credential exchange (login → token) for
HTTP devices.

The output of enrollment is *exactly* what you persist via an `IdentityStore` and later feed to
`Transport.from_identity()`.

## Who Calls `enroll()`?

In the recommended flow, **you don't call `Enrollment.enroll()` directly** — the generated
`MyDeviceClient.discover_and_connect()` classmethod calls it for you on first run, then persists the result and reuses
it forever. Your app code only constructs the adapter and hands it off:

```python
async with await MyDeviceClient.discover_and_connect(
    saved_name="kitchen",
    enrollment=HttpEnrollment(login_path="/v1/auth/login"),
) as client: ...
```

Call `Enrollment.enroll()` directly only when you need a custom flow (picking between multiple candidates on the bench,
multi-step pairing, out-of-band provisioning); see [Lifecycle](lifecycle.md#when-to-use-the-explicit-flow) for the
explicit two-script pattern.

## The Protocol

```{eval-rst}
.. autoclass:: kandra_runtime.enrollment.Enrollment
   :members:
   :no-index:
```

```{eval-rst}
.. autoclass:: kandra_runtime.enrollment.EnrollmentError
   :no-index:
```

Implementations should raise `EnrollmentError` (not return `None`) when a candidate's transport family doesn't match —
the caller catches one exception type regardless of *which* enrollment adapter rejected it.

## Built-In Adapters

`BleEnrollment` : Opens the device's BLE transport (bonding negotiated by the OS
  Bluetooth stack), reads any first-contact characteristics required
  by the device, and emits a {class}`~kandra_runtime.identity.BleIdentity`.
  The bond key itself lives in the OS keychain — Kandra never persists
  raw key material.

`HttpEnrollment` : POSTs a credentials payload to the configured login endpoint, extracts
  the auth token from the response, and emits an
  {class}`~kandra_runtime.identity.HttpIdentity` carrying that token.

## HTTP Login Flow

```{mermaid}
sequenceDiagram
    autonumber
    participant App as Your app
    participant E as HttpEnrollment
    participant D as Device

    App->>E: enroll(candidate, saved_name="kitchen")
    E->>D: POST /v1/auth/login<br/>{"username": "...", "password": "..."}
    alt Success
        D-->>E: 200 OK {"token": "eyJ..."}
        E-->>App: HttpIdentity(base_url=..., auth_token="eyJ...")
    else Bad credentials
        D-->>E: 401 Unauthorized
        E--xApp: raise EnrollmentError("login rejected")
    else No login endpoint
        D-->>E: 404 Not Found
        E--xApp: raise EnrollmentError("device does not require auth")
    end
```

For devices that don't require authentication, `HttpEnrollment` can be configured to skip the login phase and emit an
`HttpIdentity` with `auth_token=None`.

## Why a Separate Phase?

Two reasons:

1. **Cost.** Enrollment is expensive (a BLE bond can take seconds and
   prompt the user; an HTTP login burns a credential round-trip). The
   identity it produces lets every subsequent session skip it.
2. **Trust boundary.** Enrollment is the only phase that handles
   *secrets* (credentials, bond keys). Keeping it isolated makes it
   easier to audit and replace without touching the dispatch path.

## When to Write Your Own

Custom enrollment is the most common extension point in the runtime, because every device's first-contact ritual is
different (challenge–response, QR code pairing, OAuth dance, hardware button press, …). The contract is small —
implement one `async def enroll(...)` method that returns an `Identity` — so it's straightforward.

```python
class MyButtonPressEnrollment:
    async def enroll(self, candidate: Candidate, *, saved_name: str) -> Identity:
        if candidate.transport != "ble":
            raise EnrollmentError(f"not a BLE candidate: {candidate.transport}")
        # ... wait for the device to advertise the post-button-press service UUID ...
        return BleIdentity(saved_name=saved_name, address=candidate.address)
```
