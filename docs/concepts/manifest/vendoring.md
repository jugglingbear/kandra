# vendoring

**Optional.** Adjusts which files a `--profile` build copies into the SDK. Most builds never need it: the generator
follows your imports and copies what they reach.

```yaml
vendoring:
  extra_include:
    - devices/pneumatic_bear_poker/handlers/super_important.py   # imported dynamically
    - devices/pneumatic_bear_poker/assets/                      # data files
  exclude:
    - devices/pneumatic_bear_poker/handlers/super_secret.py
```

## Fields

```{manifest-fields} Vendoring
```

Each entry is a path relative to a [source root](source_roots.md): a single file, a whole folder, or a glob. It is
never a `module:Class` name.

## Common Questions

**When do I need `extra_include`?**
: When code loads a module by name at run time (`importlib.import_module(...)`), or reads data files. The generator
  can't see either: a missed module fails the build, and a missed data file is simply left out of the SDK.

**Does `exclude` override audiences?**
: It removes files for every audience. To ship a file to some audiences but not others, use
  `audience_profiles.yaml` instead. See [Audiences & IP Isolation](../audiences.md).

**Does a plain `kandra build` use this?**
: No. Without `--profile`, the SDK imports straight from your source roots and copies nothing.
