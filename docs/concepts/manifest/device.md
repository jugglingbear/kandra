# device

**Required.** Who the SDK is for: the device's id, its name, and every audience you build SDKs for.

```yaml
device:
  id: pneumatic_bear_poker
  display_name: Pneumatic Bear Poker
  audience: [internal, partner_woodland]
```

## Fields

```{manifest-fields} Device
```

## Common Questions

**What is an audience?**
: A label for a group that gets its own SDK, such as your own team (`internal`) or one partner (`partner_woodland`).
  Every command, attribute, and event says which audiences may use it, and a `--profile` build leaves out everything
  its audiences may not use. See [Audiences & IP Isolation](../audiences.md).

**Can an operation use an audience that isn't listed here?**
: No. The loader rejects an operation whose `audience` names anything missing from `device.audience`.

**What does the `id` affect?**
: The generated package name (`<id>_sdk`) and client class (`PneumaticBearPokerClient` for `pneumatic_bear_poker`).

**Does `firmware_min` stop the SDK from talking to older firmware?**
: Not yet. It is recorded for documentation only.
