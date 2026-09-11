"""Unit tests for the runtime capability-negotiation primitives."""

from __future__ import annotations

import pytest
from kandra_runtime import (
    Capabilities,
    CapabilityProbe,
    CapabilityUnavailableError,
    StaticCapabilityProbe,
)


def test_capabilities_supports_subset() -> None:
    caps = Capabilities(frozenset({"gps", "hdr", "night"}))
    assert caps.supports(["gps"])
    assert caps.supports(["gps", "hdr"])
    assert caps.supports([])  # no requirement is always satisfied
    assert not caps.supports(["gps", "missing"])
    assert not caps.supports(["absent"])


async def test_static_probe_returns_fixed_set() -> None:
    probe = StaticCapabilityProbe("gps", "hdr")
    assert isinstance(probe, CapabilityProbe)
    got = await probe.probe(client=object())
    assert got == frozenset({"gps", "hdr"})


async def test_static_probe_empty() -> None:
    probe = StaticCapabilityProbe()
    assert await probe.probe(client=None) == frozenset()


def test_capability_unavailable_error_carries_context() -> None:
    err = CapabilityUnavailableError("camera.night_photo", ["night", "hdr"])
    assert err.operation_id == "camera.night_photo"
    assert err.missing == ("night", "hdr")
    msg = str(err)
    assert "camera.night_photo" in msg
    assert "hdr" in msg and "night" in msg


def test_capability_unavailable_is_kandra_error() -> None:
    from kandra_runtime import KandraError

    with pytest.raises(KandraError):
        raise CapabilityUnavailableError("x.y", ["z"])
