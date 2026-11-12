# transports

**Required.** The ways the generated SDK can reach the device, such as HTTP over Wi-Fi or BLE. Each entry is one
connection; commands, attributes, and events pick the ones they ride by `id`.

```yaml
transports:
  - id: wifi
    family: http
```

That is a complete HTTP transport: the runtime supplies the JSON codec and the HTTP client.

## Fields

```{manifest-fields} Transport
:conditional: codec=Yes, except HTTP; channels=For BLE
```

## Families

`family` decides what else the transport needs, and which wiring block every operation on it must carry:

| `family` | The transport also needs | Each command, attribute, and event needs |
|----------|--------------------------|------------------------------------------|
| `http` | Nothing (`codec` and `adapter` are optional) | An `http:` block: method and path |
| `ble` | `codec` and `channels` | A `ble:` block naming a channel |
| `loopback` or unset | `codec` | Nothing extra (used for tests and in-process fakes) |

## channels (BLE)

A channel is a named pair of GATT characteristic UUIDs: the one the SDK writes requests to, and the one the device
notifies replies on. A BLE command picks its channel by name, so callers never see a UUID.

```yaml
transports:
  - id: radio
    family: ble
    codec: mydevice.codecs:TlvCodec
    channels:
      command: { write: "0000aa01-0000-1000-8000-00805f9b34fb", notify: "0000aa02-0000-1000-8000-00805f9b34fb" }
```

```{manifest-fields} BleChannelSpec
```

## enrollment (HTTP)

The device's login endpoint. The generated SDK bakes it into `http_enrollment()`, which `discover_and_connect()` uses by
default, so callers never hard-code the login URL. Credentials never go in the manifest; callers pass them at run time.

```yaml
transports:
  - id: wifi
    family: http
    enrollment: { login_path: /v1/auth/login }
```

```{manifest-fields} TransportEnrollment
```

## Common Questions

**Do I need a `codec` for HTTP?**
: Only if the device doesn't speak JSON. Without one, every command uses the built-in `HttpJsonCodec`. Set `codec:` on
  the transport to make your own class the default for every command, or on a single command to change just that one.
  See [Codec](../codec.md).

**Can a device have more than one transport?**
: Yes, list several (say `wifi` and `radio`). Each command lists the transports it supports in its own `transports:`
  and carries a wiring block for each.

**Where does the device's address go?**
: Not here. The SDK gets it at run time from discovery or from the saved identity. See [Scanner](../scanner.md) and
  [Identity](../identity.md).

**How do I use my own HTTP or BLE client?**
: Point `adapter:` at your class. See
  [Custom transports via the manifest](../transport.md#custom-transports-via-the-manifest).

**The device reports errors inside a `200` reply. How do I catch them?**
: Set `interpreter:`. See [Custom interpreters](../result.md#custom-interpreters).

## See Also

[Transport](../transport.md) · [Codec](../codec.md) · [Result](../result.md) · [Enrollment](../enrollment.md)
