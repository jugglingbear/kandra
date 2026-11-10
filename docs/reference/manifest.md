# Manifest Reference

Every field of the manifest YAML, generated straight from the pydantic models that validate it — so this page can
never drift from what the loader actually accepts. The [Manifest](../concepts/manifest.md) concept page is the narrative
walkthrough; this is the exhaustive field-by-field reference.

The same models also emit [`schemas/manifest.schema.json`](https://github.com/jugglingbear/kandra/blob/main/schemas/manifest.schema.json)
(`kandra schema`), which gives editors autocomplete, hover docs, and inline validation as you author a manifest.

## Editor autocomplete

Editors only use the schema once a manifest is associated with it. In VS Code, install the YAML extension
(`redhat.vscode-yaml`), write the schema into your project, and associate it in either of two ways:

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

## Fields

```{eval-rst}
.. automodule:: kandra.manifest.model
   :members:
   :member-order: bysource
   :exclude-members: model_config
```
