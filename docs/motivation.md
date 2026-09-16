# Why Kandra?

If you ship an embedded device, you probably ship an SDK with it. If you ship more than one device, or sell to more than
one partner, you quickly end up with one of the following:

- **One giant SDK** that every customer sees, with IP leakage and a bloated API surface.
- **N hand-maintained forks**, one per device or per customer, that drift apart and rot.
- **A code generator over an IDL** (protobuf, OpenAPI, …) that forces you to duplicate every type definition in a
  schema language and gives you generated code your engineers don't want to read.

Kandra picks a different tradeoff:

- **Python is the source of truth.** Your models, handlers, and codecs are normal Python classes in your repo, not
  definitions in a schema language (transports are usually the runtime's built-ins). Your IDE, ruff, and pytest work on
  them directly — there's no separate IDL to learn or keep your types in sync with.
- **YAML is wiring only.** The manifest references your Python classes by dotted path and declares which commands ship
  on which transports for which audience. It contains **no** type definitions.
- **Audience is first-class.** Every device, command, attribute, and event carries an `audience:` in the manifest, and
  a companion `audience_profiles.yaml` grants each *source file* to those audiences (an in-file `# kandra-audience:`
  header can only narrow the grant). Building with `--profile <audience>` prunes the API surface *and* the vendored
  code to that audience and leakage-scans the result, failing the build if the two disagree or anything leaks across.
  See [Audiences & IP Isolation](concepts/audiences.md).
- **One runtime, many SDKs.** Every generated package depends only on the small `kandra-runtime` PyPI package. A
  `--profile` build goes further and *vendors* (copies in) that audience's own transitive closure — the handler, codec,
  and model files it reaches plus everything they import — under an `_internal/` subpackage, so the artifact is fully
  self-contained.

## How It Works

```{mermaid}
flowchart LR
    subgraph inputs["Authoring inputs (your repo, never shipped)"]
        direction TB
        yaml["device.yaml"]
        handlers["handlers/*.py"]
        codecs["codecs/*.py"]
        models["models/*.py"]
        yaml ~~~ handlers ~~~ codecs ~~~ models
    end
    build(["kandra build"])
    subgraph artifact["Generated SDK"]
        direction TB
        client["client.py"]
        commands["commands/*.py"]
        amodels["models/*.py"]
        registry["registry.py"]
        client ~~~ commands ~~~ amodels ~~~ registry
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

A plain `kandra build` runs steps 1–2 and 6–7 and has the generated client import handlers from your `source_roots`
(the lightweight development mode). Adding `--profile <audience>` turns on the full isolation layer — the closure
restriction, audience pruning, and vendoring in steps 3–5 — emitting a self-contained package under `dist/<audience>/`.
See [Audiences & IP Isolation](concepts/audiences.md) for the full pipeline.

For the full layer breakdown and the runtime/generator split, see [Architecture](concepts/architecture.md).
