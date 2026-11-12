# `kandra` (generator) API

The build-time generator. Run via `kandra build <manifest.yaml>`; the underlying API is also importable for advanced
integration.

## Manifest Model

The pydantic models that validate a manifest. For what each key means and how to fill it in, start from the
[Manifest](../concepts/manifest.md) page.

```{eval-rst}
.. automodule:: kandra.manifest.model
   :members:
   :member-order: bysource
   :exclude-members: model_config
```

## Loader

```{eval-rst}
.. automodule:: kandra.loader
   :members:
```

## Generator

```{eval-rst}
.. automodule:: kandra.generator
   :members:
```

## Audience &amp; IP Isolation

The `--profile` pipeline (see [Audiences &amp; IP Isolation](../concepts/audiences.md)): audience policy, import-closure
walking, vendoring with namespace rewrite, the leakage scan, and the `kandra audit` report.

```{eval-rst}
.. automodule:: kandra.audience
   :members:

.. automodule:: kandra.closure
   :members:

.. automodule:: kandra.vendor
   :members:

.. automodule:: kandra.leakage
   :members:

.. automodule:: kandra.audit
   :members:
```
