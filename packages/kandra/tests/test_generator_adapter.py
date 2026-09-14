"""Tests for the transport ``adapter:`` field -- custom adapters vs the runtime default.

The runtime-default path (``adapter`` omitted -> ``HttpTransport`` / ``BleTransport``)
is covered by the connect / lifecycle suites building the ``ble_widget`` fixture.
Here we cover the *custom* path: a transport with ``adapter:`` set must use that
class instead of the runtime transport.
"""

from __future__ import annotations

from pathlib import Path

from kandra.generator import build_sdk

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "custom_adapter"
FIXTURE_MANIFEST = FIXTURE_DIR / "manifest.yaml"


def test_custom_adapter_replaces_runtime_transport(tmp_path: Path) -> None:
    """A transport with ``adapter:`` set is wired (mypy-strict clean), not the runtime default."""
    result = build_sdk(FIXTURE_MANIFEST, output_root=tmp_path, typecheck=True)
    client_src = (result.package_path / "client.py").read_text(encoding="utf-8")

    assert "from custom_adapter.device import CustomHttpTransport as _Adapter_http" in client_src
    assert "_Adapter_http.from_identity(ident)" in client_src
    # The only HTTP transport is custom, so the runtime HttpTransport is not imported.
    assert "    HttpTransport,\n" not in client_src
