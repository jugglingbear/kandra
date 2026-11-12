# schema_version

**Required.** The manifest format version. It is always `1`; the loader rejects any other value.

```yaml
schema_version: 1
```

```{manifest-fields} Manifest
:fields: schema_version
```

## Common Questions

**Why does it exist if it's always 1?**
: So a future Kandra can change the manifest format and still recognize, and reject or upgrade, older files.
