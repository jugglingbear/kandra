# kandra

Code generator + CLI for the Kandra framework. Consumes a device manifest plus your handler/transport/codec Python
classes and emits a typed, audience-filtered SDK package that depends only on
[`kandra-runtime`](../kandra-runtime/).

This package is the **build-time** tool. Generated SDKs do not depend on it — they depend only on `kandra-runtime`.

## Install

```bash
pip install kandra              # adds the `kandra` CLI
```

## Usage

```bash
kandra validate manifest.yaml
kandra build manifest.yaml --output-dir ./src/my_device_sdk_gen --clean
kandra schema > manifest.schema.json
```

See the [main repository README](../../README.md) for the framework overview.
