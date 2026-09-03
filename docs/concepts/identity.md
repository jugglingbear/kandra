# Identity

An **Identity** is the set of facts the runtime needs to reconnect to a device you've already met. It is *not* a session
or a connection — it's the persistent record (BLE address, HTTP base URL, auth token, Wi-Fi SSID, …) that lets a future
`Transport.from_identity(identity)` call rebuild the live channel.

Identities are produced by [Enrollment](enrollment.md), persisted by an **IdentityStore**, and consumed by
[Transport](transport.md).

## Class Diagram

```{mermaid}
classDiagram
    direction BT

    class BaseModel {
        <<pydantic>>
    }
    class _IdentityBase {
        +saved_name: str
        +enrolled_at: datetime
    }
    class BleIdentity {
        +transport: "ble"
        +address: str
        +advertised_name: str?
    }
    class HttpIdentity {
        +transport: "http"
        +base_url: str
        +auth_token: str?
    }
    class CompositeIdentity {
        +transport: "composite"
        +components: dict~str, Identity~
        +wifi: WifiCredentials?
    }
    class WifiCredentials {
        +ssid: str
        +password: str
    }

    BaseModel <|-- _IdentityBase
    _IdentityBase <|-- BleIdentity
    _IdentityBase <|-- HttpIdentity
    _IdentityBase <|-- CompositeIdentity
    BaseModel <|-- WifiCredentials
    CompositeIdentity o-- WifiCredentials : wifi
    CompositeIdentity o-- "*" _IdentityBase : components
```

## The Discriminated Union

`Identity` itself is a Pydantic v2 **discriminated union** keyed on the `transport` literal:

```python
Identity = Annotated[
    BleIdentity | HttpIdentity | CompositeIdentity,
    Field(discriminator="transport"),
]
```

This means JSON round-trips are exact and unambiguous — the `transport` field tells the parser which concrete subclass
to instantiate:

```json
{"transport": "http", "saved_name": "kitchen",
 "base_url": "http://192.168.1.42", "auth_token": "tok_..."}
```

All identity classes are `frozen=True, extra="forbid"`. You cannot mutate a saved identity in place, and unknown fields
are a parse error — protecting the on-disk format from silent drift.

## CompositeIdentity: When One Device Needs More Than One Transport

An action camera, for example, is controlled over BLE but transfers media over Wi-Fi. A wall-plug switch might pair over
BLE and then expose its control API over HTTP. `CompositeIdentity` groups those facts under one `saved_name`:

```python
identity = CompositeIdentity(
    saved_name="my-camera",
    components={
        "control": BleIdentity(saved_name="my-camera", address="AA:BB:..."),
        "media": HttpIdentity(saved_name="my-camera", base_url="http://10.5.5.9"),
    },
    wifi=WifiCredentials(ssid="my-camera-7f3a", password="abcd1234"),
)
```

The generator surfaces the `components` keys as keyword arguments to `Transport.from_identity()` so the right
sub-transport is wired up.

## IdentityStore Protocol

```{eval-rst}
.. autoclass:: kandra_runtime.identity.IdentityStore
   :members:
   :no-index:
```

The default implementation, {class}`~kandra_runtime.identity_store_file.PlatformDirsJsonStore`, writes to the
OS-appropriate user-config directory (via `platformdirs`) using an atomic-rename strategy that's safe for the typical
single-process CLI case.

For sandbox scripts, tutorials, and unit tests, the runtime also ships
{class}`~kandra_runtime.testing.MemoryIdentityStore` — an in-memory implementation that keeps records in a plain
`dict`, has zero disk side effects, and dies with the process:

```python
from kandra_runtime import HttpIdentity
from kandra_runtime.testing import MemoryIdentityStore

store = MemoryIdentityStore()
store.save(HttpIdentity(saved_name="dev", base_url="http://localhost:8080"))

client = await MyDeviceClient.connect("dev", store=store)
```

Custom backends (Redis, SQLite, OS keychain) only need to satisfy the four-method `Protocol` — no inheritance required,
thanks to `@runtime_checkable`.

## Error Type

```{eval-rst}
.. autoclass:: kandra_runtime.identity.IdentityNotFoundError
   :no-index:
```

Subclasses the built-in `LookupError` so existing exception handlers keep working.
