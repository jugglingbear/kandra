"""Sphinx directive that renders a manifest model's fields as a table, generated from the pydantic model.

The table can never drift from what the loader accepts: field names, types, defaults, and descriptions all come from
:mod:`kandra.manifest.model`. Usage (MyST)::

    ```{manifest-fields} Transport
    :conditional: codec=Yes, except HTTP; channels=For BLE
    ```

``:conditional:`` overrides the *Required* cell for fields whose requirement depends on other fields, which the model's
own ``is_required()`` cannot express. ``:fields: a, b`` limits the table to those fields.
"""

from __future__ import annotations

import types
import typing
from typing import Any, ClassVar, Literal, Union, get_args, get_origin

from docutils import nodes
from docutils.parsers.rst import directives
from kandra.manifest import model as manifest_model
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective

_SCALARS: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}
_MAX_NESTED_KEYS = 4


def _type_name(annotation: Any) -> str:
    """Describe a field annotation in YAML terms, e.g. ``list of string`` or ``one of `GET`, `POST```."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is typing.Annotated:
        return _type_name(args[0])
    if origin in (Union, types.UnionType):
        return " or ".join(_type_name(a) for a in args if a is not type(None))
    if origin is Literal:
        return "one of " + ", ".join(f"`{a}`" for a in args)
    if origin is list:
        return f"list of {_type_name(args[0])}"
    if origin is dict:
        return f"map of name → {_type_name(args[1])}"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        keys = list(annotation.model_fields)
        shown = ", ".join(keys[:_MAX_NESTED_KEYS]) + (", …" if len(keys) > _MAX_NESTED_KEYS else "")
        return f"{{{shown}}}"
    return _SCALARS.get(annotation, getattr(annotation, "__name__", str(annotation)))


def _default(field: FieldInfo) -> str:
    """Return the field's default as inline code, or an empty string when there is none worth showing."""
    if field.is_required():
        return ""
    value = field.default_factory() if field.default_factory is not None else field.default
    if value is None or value == [] or value == {}:
        return ""
    return f"`{str(value).lower() if isinstance(value, bool) else value}`"


class ManifestFields(SphinxDirective):
    """``manifest-fields`` directive: one table row per field of a :mod:`kandra.manifest.model` class."""

    required_arguments = 1
    option_spec: ClassVar[dict[str, Any]] = {"conditional": directives.unchanged, "fields": directives.unchanged}

    def run(self) -> list[nodes.Node]:
        """Build the table for the model named by the directive argument."""
        model = getattr(manifest_model, self.arguments[0], None)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise self.error(f"manifest-fields: {self.arguments[0]!r} is not a model in kandra.manifest.model")
        conditional = self._conditional(model)
        rows = ["* - Field\n  - Required\n  - Type\n  - What it does"]
        for name, field in self._selected(model).items():
            required = conditional.get(name, "Yes" if field.is_required() else "No")
            default = _default(field)
            kind = _type_name(field.annotation) + (f" (default {default})" if default else "")
            description = " ".join((field.description or "").split())
            rows.append(f"* - `{name}`\n  - {required}\n  - {kind}\n  - {description}")
        table = "```{list-table}\n:header-rows: 1\n:widths: 16 14 22 48\n\n" + "\n".join(rows) + "\n```"
        return self.parse_text_to_nodes(table)

    def _selected(self, model: type[BaseModel]) -> dict[str, FieldInfo]:
        """Return the fields named by ``:fields:`` (all fields when the option is absent)."""
        names = [n.strip() for n in self.options.get("fields", "").split(",") if n.strip()]
        unknown = [n for n in names if n not in model.model_fields]
        if unknown:
            raise self.error(f"manifest-fields: {model.__name__} has no field(s) {unknown}")
        return {n: model.model_fields[n] for n in names} if names else dict(model.model_fields)

    def _conditional(self, model: type[BaseModel]) -> dict[str, str]:
        """Parse ``:conditional: field=text; field=text`` and reject names the model does not have."""
        overrides: dict[str, str] = {}
        for item in filter(None, (part.strip() for part in self.options.get("conditional", "").split(";"))):
            name, _, text = item.partition("=")
            if name.strip() not in model.model_fields or not text.strip():
                raise self.error(f"manifest-fields: bad :conditional: entry {item!r} for {model.__name__}")
            overrides[name.strip()] = text.strip()
        return overrides


def setup(app: Sphinx) -> dict[str, Any]:
    """Register the ``manifest-fields`` directive."""
    app.add_directive("manifest-fields", ManifestFields)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
