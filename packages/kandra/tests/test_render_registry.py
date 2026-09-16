"""Unit tests for pure registry rendering (:mod:`kandra.generator.render`).

The build-level tests in :mod:`test_build_errors` cover the HTTP per-command codec
override end to end. There is no BLE example/fixture to build against, so the BLE
override branch is exercised here against the pure ``render_registry`` function.
"""

from __future__ import annotations

from kandra.generator.render import BleCommandWire, CommandSpec, TransportSpec, render_registry

_BLE_TRANSPORT = TransportSpec(
    transport_id="ble",
    enum_member="BLE",
    family="ble",
    codec_import="from dev.codec import TransportCodec as _TransportCodec",
    codec_alias="_TransportCodec",
    channels=(("cmd", "0000-write", "0000-notify"),),
)


def _ble_command(wire: BleCommandWire) -> CommandSpec:
    return CommandSpec(
        command_id="ping.check",
        namespace="ping",
        method="check",
        timeout=None,
        request_import="from dev.h import Req as _Req",
        request_alias="_Req",
        response_import="from dev.h import Resp as _Resp",
        response_alias="_Resp",
        transports=["ble"],
        ble_wires={"ble": wire},
    )


def test_ble_command_uses_transport_codec_by_default() -> None:
    cmd = _ble_command(BleCommandWire(channel="cmd", expects_response=True, timeout=None))
    registry = render_registry([cmd], [_BLE_TRANSPORT])
    assert "payload_codec=_TransportCodec(_Req, _Resp)" in registry


def test_ble_command_codec_override_replaces_transport_codec() -> None:
    wire = BleCommandWire(
        channel="cmd",
        expects_response=True,
        timeout=None,
        codec_import="from dev.override import OverrideCodec as _OverrideCodec",
        codec_alias="_OverrideCodec",
    )
    registry = render_registry([_ble_command(wire)], [_BLE_TRANSPORT])
    assert "from dev.override import OverrideCodec as _OverrideCodec" in registry
    assert "payload_codec=_OverrideCodec(_Req, _Resp)" in registry
    assert "payload_codec=_TransportCodec(_Req, _Resp)" not in registry
