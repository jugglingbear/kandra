# attributes

**Optional.** Named device state that callers can read, write, or subscribe to, such as a setting. Each attribute
becomes an object with one method per operation: `settings.poke_intensity` gives
`client.settings.poke_intensity.read()` and `.write(value)`.

```yaml
attributes:
  - id: settings.poke_intensity
    handler: devices.pneumatic_bear_poker.handlers.settings:PokeIntensitySetting
    transports: [wifi]
    operations: [read, write]
    audience: [internal, partner_woodland]
    http:
      wifi:
        read: { method: GET, path: /v1/settings/poke_intensity }
        write: { method: PUT, path: /v1/settings/poke_intensity }
```

The handler names the value type. Add `write_ack` only when the device replies to a write with something other than the
value itself:

```python
@dataclass(frozen=True)
class PokeIntensity:
    level: int


class PokeIntensitySetting:
    """How hard the arm pokes, on a 0-11 scale."""

    value = PokeIntensity
```

## Fields

```{manifest-fields} Attribute
:conditional: http=For each HTTP transport; ble=For each BLE transport
```

## http (per HTTP transport)

One entry per operation listed in `operations`. Keyed by transport id.

```{manifest-fields} HttpAttributeSpec
:conditional: read=If listed in `operations`; write=If listed in `operations`; subscribe=If listed in `operations`
```

### read and write

```{manifest-fields} HttpAttributeOp
```

### subscribe

```yaml
        subscribe: { mode: poll, path: /v1/settings/poke_intensity, interval: 2.0 }
```

```{manifest-fields} HttpAttributeSubscribe
:conditional: interval=For `poll`
```

## ble (per BLE transport)

One channel carries reads, writes, and update notifications. Keyed by transport id.

```{manifest-fields} BleAttributeSpec
```

## Common Questions

**Attribute or command?**
: An attribute is a value the device keeps (a setting, a status). A command is an action (deploy, power off). If you'd
  describe it with a noun, it's probably an attribute.

**Can an attribute be read-only?**
: Yes: `operations: [read]`. The SDK then has no `write()` for it.

**Writes report errors differently from reads. What do I set?**
: `interpreter:` in the `write` block. It replaces the transport's interpreter for writes only. See
  [Custom interpreters](../result.md#custom-interpreters).

**Does subscribe poll the device?**
: Only with `mode: poll`, which needs an `interval`. The default, `mode: sse`, holds one stream open and receives
  updates as the device sends them. Subscriptions are async-only; the sync client offers `read()` and `write()`.

## See Also

[Attribute](../attribute.md) · [commands](commands.md) · [events](events.md)
