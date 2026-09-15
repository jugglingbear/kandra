# Manifest Reference

Every field of the manifest YAML, generated straight from the pydantic models that validate it — so this page can
never drift from what the loader actually accepts. The [Manifest](../concepts/manifest.md) concept page is the narrative
walkthrough; this is the exhaustive field-by-field reference.

The same models also emit [`schemas/manifest.schema.json`](https://github.com/jugglingbear/kandra/blob/main/schemas/manifest.schema.json)
(`kandra schema`), which gives editors autocomplete and inline validation as you author a manifest.

```{eval-rst}
.. automodule:: kandra.manifest.model
   :members:
   :member-order: bysource
   :exclude-members: model_config
```
