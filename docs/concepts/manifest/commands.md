# commands

**Optional** (a manifest needs at least one command, attribute, or event). One-shot actions the SDK can send, such as
"extend the arm". Each command becomes a method: `poker.deploy` is called as `client.poker.deploy(...)`.

```yaml
commands:
  - id: poker.deploy
    handler: devices.pneumatic_bear_poker.handlers.poker:Deploy
    transports: [wifi]
    audience: [internal, partner_woodland]
    http:
      wifi:
        method: POST
        path: /v1/poker/deploy
```

The handler is a plain Python class that names the request and reply types. Its docstring becomes the docstring of the
generated method:

```python
@dataclass(frozen=True)
class DeployRequest:
    pressure_psi: int


@dataclass(frozen=True)
class DeployResponse:
    delivered_psi: int


class Deploy:
    """Extend the arm at the requested pressure (100 psi max)."""

    request = DeployRequest
    response = DeployResponse
```

## Fields

```{manifest-fields} Command
:conditional: http=For each HTTP transport; ble=For each BLE transport
```

## http (per HTTP transport)

How the command goes over HTTP. Keyed by transport id, so a command on two HTTP transports has two blocks.

```{manifest-fields} HttpCommandSpec
```

## ble (per BLE transport)

Which channel the command uses on a BLE transport. Keyed by transport id.

```yaml
    ble:
      radio:
        channel: command
```

```{manifest-fields} BleCommandSpec
```

## Common Questions

**Do I need a handler?**
: Yes. Every command names one; it tells the generator the request and reply types. A command that takes no arguments
  uses an empty dataclass as its request.

**The device takes arguments in the URL, not a body. What do I set?**
: `query_from_request: true` in the `http` block. The request's fields become query parameters.

**The device cuts the connection before replying (e.g. a power-off). What do I set?**
: `expects_response: false`. The SDK treats a missing reply as success and returns `None`.

**Will the SDK retry a command that fails?**
: Only if you set `idempotent: true` and `retries: N`, and only after a dropped connection or a timeout. A refused
  command is never retried, and neither is a rejected login: that raises
  [`IdentityStaleError`](../lifecycle.md#credential-staleness--re-enrollment) so the client can log in again.

**How do I keep a command out of a partner's SDK?**
: Leave that partner's audience out of the command's `audience`. See [Audiences & IP Isolation](../audiences.md).

**How do I hide a command on devices that lack a feature?**
: List the feature in `capabilities`. See [Capabilities](../capabilities.md).

## See Also

[Codec](../codec.md) · [Result](../result.md) · [attributes](attributes.md) · [events](events.md)
