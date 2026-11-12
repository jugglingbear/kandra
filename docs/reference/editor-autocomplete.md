# Editor Autocomplete

Kandra publishes a JSON schema for manifests,
[`schemas/manifest.schema.json`](https://github.com/jugglingbear/kandra/blob/main/schemas/manifest.schema.json),
generated from the same models the loader uses. Point your editor at it and you get autocomplete, hover descriptions,
and inline errors while you write a manifest.

In VS Code, install the YAML extension (`redhat.vscode-yaml`), write the schema into your project, and associate it in
either of two ways:

```bash
poetry run kandra schema > manifest.schema.json   # re-run after upgrading Kandra
```

```json
// .vscode/settings.json -- applies to every matching manifest in the workspace
"yaml.schemas": { "./manifest.schema.json": ["**/manifest.yaml"] }
```

```yaml
# Or per file, as the first line of the manifest:
# yaml-language-server: $schema=./manifest.schema.json
```

The Kandra repo itself is already wired this way for its example manifests.
