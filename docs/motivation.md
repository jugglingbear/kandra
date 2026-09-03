# Why Kandra?

If you ship an embedded device, you probably ship an SDK with it. If you ship more than one device, or sell to more than
one partner, you quickly end up with one of the following:

- **One giant SDK** that every customer sees, with IP leakage and a bloated API surface.
- **N hand-maintained forks**, one per device or per customer, that drift apart and rot.
- **A code generator over an IDL** (protobuf, OpenAPI, …) that forces you to duplicate every type definition in a
  schema language and gives you generated code your engineers don't want to read.

Kandra picks a different tradeoff:

- **Python is the source of truth.** Models, handlers, transports, and codecs are normal Python written in your repo.
  You refactor with the IDE, lint with ruff, and test with pytest. No schema language.
- **YAML is wiring only.** The manifest references your Python classes by dotted path and declares which commands ship
  on which transports for which audience. It contains **no** type definitions.
- **Audience is first-class.** Each command, attribute, and module is tagged. The generator emits one SDK per audience,
  and a final subprocess import-check fails the build if anything leaks across.
- **One runtime, many SDKs.** The generated package depends on a small `kandra-runtime` PyPI package and otherwise
  vendors only its own transitive code closure.

## How It Works

```{mermaid}
flowchart LR
    subgraph inputs["Authoring inputs (your repo, never shipped)"]
        yaml["device.yaml"]
        handlers["handlers/*.py"]
        transports["transports/*.py"]
        codecs["codecs/*.py"]
        models["models/*.py"]
    end
    build(["kandra build"])
    subgraph artifact["Per-audience artifact"]
        client["client.py"]
        commands["commands/*.py"]
        amodels["models/*.py"]
        registry["registry.py"]
    end
    runtime[("kandra-runtime (pip)")]
    inputs --> build --> artifact
    artifact -. depends only on .-> runtime
```

For each manifest the generator:

1. Loads the YAML into a pydantic model.
2. Imports the handler/transport/codec classes named in the manifest.
3. Walks their import closure, restricted to the `source_roots` listed in the manifest (never stdlib, third-party, or
   unlisted packages).
4. Audience-filters the closure.
5. Vendors the surviving files into `dist/<audience>/<sdk_pkg>/`.
6. Emits a thin typed facade so consumers call typed methods like `await client.<group>.<command>(...)` instead of the
   low-level `dispatch(cmd, …)`.
7. Verifies the generated package compiles and imports cleanly in an isolated subprocess before the build succeeds —
   a manifest or handler flaw that would emit a broken SDK fails the build instead of shipping.

For the full layer breakdown and the runtime/generator split, see [Architecture](concepts/architecture.md).
