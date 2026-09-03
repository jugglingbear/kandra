"""Code generator: emits a typed, transport-isolated Python SDK from a manifest."""

from __future__ import annotations

from kandra.generator.build import BuildError, build_sdk

__all__ = ["BuildError", "build_sdk"]
