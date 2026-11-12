# events

**Optional.** Things the device announces that callers subscribe to, such as "button pressed". An event has no stored
value to read or write. Each one becomes a subscription: `alerts.bear_stirred` gives
`client.alerts.bear_stirred.subscribe()`. (The reference example has no events, so this one is illustrative.)

```yaml
events:
  - id: alerts.bear_stirred
    handler: mydevice.handlers.alerts:BearStirredAlert
    transports: [wifi]
    audience: [internal]
    http:
      wifi: { mode: sse, path: /v1/alerts/bear_stirred }
```

The handler names the type of each announcement:

```python
@dataclass(frozen=True)
class BearStirred:
    magnitude: int


class BearStirredAlert:
    """The bear stirred near the arm."""

    payload = BearStirred
```

## Fields

```{manifest-fields} Event
:conditional: http=For each HTTP transport; ble=For each BLE transport
```

## http (per HTTP transport)

Keyed by transport id.

```{manifest-fields} HttpEventSpec
:conditional: interval=For `poll`
```

## ble (per BLE transport)

Keyed by transport id.

```{manifest-fields} BleEventSpec
```

## Common Questions

**Event or attribute?**
: If the device keeps a value you could read at any time, it's an attribute; subscribe to it for changes. If it only
  tells you that something happened, it's an event.

**Can I call `subscribe()` without `await`?**
: No. Subscriptions are async-only; the sync client leaves events out.

## See Also

[Event](../event.md) · [attributes](attributes.md)
