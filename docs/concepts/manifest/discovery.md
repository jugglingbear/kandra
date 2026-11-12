# discovery

**Optional.** How the SDK's `scan_http()` and `scan_ble()` helpers find the device. Without it, callers must already
know the device's address.

```yaml
discovery:
  http:
    base_urls: ["http://192.168.1.1:8080", "http://pneumatic-bear-poker.local:8080"]
    probe_path: /v1/status
    server_header_prefix: PneumaticBearPoker
```

## Fields

```{manifest-fields} DiscoverySpec
:conditional: ble=At least one of the two; http=At least one of the two
```

## http

`scan_http()` requests `probe_path` at every address in `base_urls`. Each address that answers (status below 400) and
matches `server_header_prefix`, if set, is a found device.

```{manifest-fields} HttpDiscoverySpec
```

## ble

`scan_ble()` listens for advertisements and keeps the ones that match every field you set.

```yaml
discovery:
  ble:
    name_prefix: "Bear Poker "
    service_uuids: ["0000aa00-0000-1000-8000-00805f9b34fb"]
```

```{manifest-fields} BleDiscoverySpec
```

## Common Questions

**My test device is somewhere else. Do I edit the manifest?**
: No. Pass the addresses at run time: `await scan_http(timeout=5, base_urls=["http://127.0.0.1:8080"])`.

**Can I declare `ble` discovery for an HTTP-only device?**
: No. Each block needs a transport of the same family.

**Does the probe path need a login?**
: It must not. Scanning happens before the SDK logs in.

## See Also

[Scanner](../scanner.md) · [transports](transports.md)
