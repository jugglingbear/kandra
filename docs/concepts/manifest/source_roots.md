# source_roots

**Required.** The folders that hold your Python code: handlers, codecs, and any custom transport or interpreter
classes. Paths are relative to the manifest.

```yaml
source_roots:
  - src
```

```{manifest-fields} Manifest
:fields: source_roots
```

Every `module:Class` path in the manifest is looked up inside these folders. With `src` as a root,
`devices.pneumatic_bear_poker.handlers.poker:Deploy` means the class `Deploy` in
`src/devices/pneumatic_bear_poker/handlers/poker.py`.

## Common Questions

**Can code live outside the roots?**
: No. Every class the manifest names must be under a source root, or the build fails. This is what lets a `--profile`
  build copy exactly the files the SDK needs (see [vendoring](vendoring.md)).

**Several devices share some code. Where does it go?**
: In a shared folder that every device's manifest lists as a root, e.g. `src` holding both `src/devices/` and
  `src/common/`. A manifest can list more than one root.
