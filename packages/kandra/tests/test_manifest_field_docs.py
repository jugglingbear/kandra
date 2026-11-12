"""Every manifest field needs a description: the docs' field tables and the editor schema are generated from them."""

from __future__ import annotations

import inspect

from kandra.manifest import model
from pydantic import BaseModel


def test_every_manifest_field_has_a_description() -> None:
    """A new field without a docstring would show up blank in the docs and in editor tooltips."""
    models = [
        obj
        for obj in vars(model).values()
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj.__module__ == model.__name__
    ]
    missing = [
        f"{m.__name__}.{name}"
        for m in models
        for name, f in m.model_fields.items()
        if not (f.description or "").strip()
    ]
    assert not missing, f"manifest fields without a description: {missing}"
