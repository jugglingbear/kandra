"""Validation-branch tests for the manifest model (per-transport blocks, cross-refs)."""

from __future__ import annotations

import pytest
from kandra import LoaderError, load_manifest

_BASE_DEVICE = "schema_version: 1\ndevice:\n  id: d\n  display_name: D\n  audience: [internal]\nsource_roots: [src]\n"


def _load(body: str):  # type: ignore[no-untyped-def]
    return load_manifest(_BASE_DEVICE + body)


# ---------------------------------------------------------------------------
# Happy paths that exercise otherwise-uncovered validators.
# ---------------------------------------------------------------------------


def test_transport_auth_handler_is_validated() -> None:
    manifest = _load(
        "transports:\n"
        "  - id: http\n    adapter: a.b:C\n    codec: a.b:D\n    family: http\n    auth: {handler: 'auth.mod:Login'}\n"
        "commands:\n"
        "  - id: x.y\n    handler: a.b:H\n    transports: [http]\n    audience: [internal]\n"
        "    http:\n      http: {method: GET, path: /}\n"
    )
    assert manifest.transports[0].auth is not None
    assert manifest.transports[0].auth.handler == "auth.mod:Login"


def test_http_transport_may_omit_codec() -> None:
    # HTTP uses the runtime HttpJsonCodec, so `codec` is optional for HTTP.
    manifest = _load(
        "transports:\n  - {id: http, family: http}\n"
        "commands:\n"
        "  - id: x.y\n    handler: a.b:H\n    transports: [http]\n    audience: [internal]\n"
        "    http:\n      http: {method: PUT, path: /}\n"
    )
    assert manifest.transports[0].codec is None


def test_non_http_transport_requires_codec() -> None:
    with pytest.raises(LoaderError, match="'codec' is required"):
        _load(
            "transports:\n  - {id: loop, family: loopback}\n"
            "commands:\n"
            "  - id: x.y\n    handler: a.b:H\n    transports: [loop]\n    audience: [internal]\n"
        )


def test_wellformed_attribute_loads_and_validates() -> None:
    # A fully-wired attribute now loads (validation runs); the generator refuses
    # separately until attribute codegen lands.
    manifest = _load(
        "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
        "attributes:\n"
        "  - id: settings.res\n    handler: a.b:Attr\n    transports: [http]\n"
        "    operations: [read, write, subscribe]\n    audience: [internal]\n"
        "    http:\n      http:\n"
        "        read: {method: GET, path: /res}\n"
        "        write: {method: GET, path: /res, query_from_request: true}\n"
        "        subscribe: {mode: poll, path: /res, interval: 0.5}\n"
    )
    assert manifest.attributes[0].id == "settings.res"
    assert manifest.attributes[0].http["http"].write is not None


def test_attribute_missing_http_op_block_rejected() -> None:
    with pytest.raises(LoaderError, match="operation 'read' is declared but http.http.read is missing"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [http]\n"
            "    operations: [read]\n    audience: [internal]\n"
            "    http:\n      http:\n        write: {method: PUT, path: /res}\n"
        )


def test_attribute_http_transport_missing_block_rejected() -> None:
    with pytest.raises(LoaderError, match="rides http transport 'http' but has no http.http block"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [http]\n"
            "    operations: [read]\n    audience: [internal]\n"
        )


def test_attribute_http_block_on_non_http_family_rejected() -> None:
    with pytest.raises(LoaderError, match="http block on transport .* but family is"):
        _load(
            "transports:\n  - {id: loop, adapter: a.b:C, codec: a.b:D, family: loopback}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [loop]\n"
            "    operations: [read]\n    audience: [internal]\n"
            "    http:\n      loop:\n        read: {method: GET, path: /res}\n"
        )


def test_attribute_ble_undeclared_channel_rejected() -> None:
    with pytest.raises(LoaderError, match="is not declared in transport"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {q: {write: w, notify: n}}}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [ble]\n"
            "    operations: [read]\n    audience: [internal]\n"
            "    ble:\n      ble: {channel: nope}\n"
        )


def test_attribute_ble_transport_missing_block_rejected() -> None:
    with pytest.raises(LoaderError, match="rides ble transport 'ble' but has no ble.ble block"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {q: {write: w, notify: n}}}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [ble]\n"
            "    operations: [read]\n    audience: [internal]\n"
        )


def test_attribute_subscribe_poll_requires_interval() -> None:
    with pytest.raises(LoaderError, match="mode='poll' requires an 'interval'"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [http]\n"
            "    operations: [subscribe]\n    audience: [internal]\n"
            "    http:\n      http:\n        subscribe: {mode: poll, path: /res}\n"
        )


def test_attribute_subscribe_sse_rejects_interval() -> None:
    with pytest.raises(LoaderError, match="'interval' only applies to mode='poll'"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [http]\n"
            "    operations: [subscribe]\n    audience: [internal]\n"
            "    http:\n      http:\n        subscribe: {mode: sse, path: /res, interval: 1.0}\n"
        )


def test_attribute_undefined_transport_rejected() -> None:
    with pytest.raises(LoaderError, match="attribute 'settings.res' references undefined transport"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [ghost]\n"
            "    operations: [read]\n    audience: [internal]\n"
        )


def test_attribute_http_block_names_untransported_id_rejected() -> None:
    with pytest.raises(LoaderError, match="http block names transport 'http2' which is not in"):
        _load(
            "transports:\n"
            "  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "  - {id: http2, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "attributes:\n"
            "  - id: settings.res\n    handler: a.b:Attr\n    transports: [http]\n"
            "    operations: [read]\n    audience: [internal]\n"
            "    http:\n"
            "      http: {read: {method: GET, path: /r}}\n"
            "      http2: {read: {method: GET, path: /r2}}\n"
        )


def test_wellformed_event_loads_and_validates() -> None:
    manifest = _load(
        "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
        "events:\n"
        "  - id: recording.state_changed\n    handler: a.b:Evt\n    transports: [http]\n"
        "    audience: [internal]\n"
        "    http:\n      http: {mode: sse, path: /events}\n"
    )
    assert manifest.events[0].id == "recording.state_changed"
    assert manifest.events[0].http["http"].mode == "sse"


def test_event_missing_http_block_rejected() -> None:
    with pytest.raises(LoaderError, match="event 'button.pressed' rides http transport 'http' but has no http.http"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "events:\n  - id: button.pressed\n    handler: a.b:Evt\n    transports: [http]\n    audience: [internal]\n"
        )


def test_event_http_block_on_non_http_family_rejected() -> None:
    with pytest.raises(LoaderError, match="event 'button.pressed': http block on transport 'ble' but family is 'ble'"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {q: {write: w, notify: n}}}\n"
            "events:\n  - id: button.pressed\n    handler: a.b:Evt\n    transports: [ble]\n    audience: [internal]\n"
            "    http:\n      ble: {mode: sse, path: /e}\n    ble:\n      ble: {channel: q}\n"
        )


def test_event_ble_undeclared_channel_rejected() -> None:
    with pytest.raises(LoaderError, match="event 'button.pressed': ble.ble.channel='ghost' is not declared"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {q: {write: w, notify: n}}}\n"
            "events:\n  - id: button.pressed\n    handler: a.b:Evt\n    transports: [ble]\n    audience: [internal]\n"
            "    ble:\n      ble: {channel: ghost}\n"
        )


def test_event_ble_transport_missing_block_rejected() -> None:
    with pytest.raises(LoaderError, match="event 'button.pressed' rides ble transport 'ble' but has no ble.ble"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {q: {write: w, notify: n}}}\n"
            "events:\n  - id: button.pressed\n    handler: a.b:Evt\n    transports: [ble]\n    audience: [internal]\n"
        )


def test_event_subscribe_poll_requires_interval() -> None:
    with pytest.raises(LoaderError, match="http event subscribe mode='poll' requires an 'interval'"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "events:\n  - id: button.pressed\n    handler: a.b:Evt\n    transports: [http]\n    audience: [internal]\n"
            "    http:\n      http: {mode: poll, path: /e}\n"
        )


def test_event_undefined_transport_rejected() -> None:
    with pytest.raises(LoaderError, match="event 'button.pressed' references undefined transport"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "events:\n  - id: button.pressed\n    handler: a.b:Evt\n    transports: [ghost]\n    audience: [internal]\n"
        )


def test_id_collision_across_sections_rejected() -> None:
    with pytest.raises(LoaderError, match="ids must be unique across commands/attributes/events"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "commands:\n  - {id: sensor.reading, handler: a.b:H, transports: [http], audience: [internal], "
            "http: {http: {method: GET, path: /}}}\n"
            "events:\n  - id: sensor.reading\n    handler: a.b:Evt\n    transports: [http]\n    audience: [internal]\n"
            "    http:\n      http: {mode: sse, path: /e}\n"
        )



# ---------------------------------------------------------------------------
# Rejection paths.
# ---------------------------------------------------------------------------


def test_channels_require_ble_family() -> None:
    with pytest.raises(LoaderError, match="channels are only valid when family='ble'"):
        _load(
            "transports:\n"
            "  - id: http\n    adapter: a.b:C\n    codec: a.b:D\n    family: http\n"
            "    channels: {cmd: {write: w, notify: n}}\n"
            "commands:\n  - {id: x.y, handler: a.b:H, transports: [http], audience: [internal]}\n"
        )


def test_duplicate_command_ids_rejected() -> None:
    with pytest.raises(LoaderError, match="duplicate ids in commands"):
        _load(
            "transports:\n  - {id: loop, adapter: a.b:C, codec: a.b:D, family: loopback}\n"
            "commands:\n"
            "  - {id: x.y, handler: a.b:H, transports: [loop], audience: [internal]}\n"
            "  - {id: x.y, handler: a.b:H2, transports: [loop], audience: [internal]}\n"
        )


def test_retries_without_idempotent_rejected() -> None:
    with pytest.raises(LoaderError, match="requires 'idempotent"):
        _load(
            "transports:\n  - {id: loop, adapter: a.b:C, codec: a.b:D, family: loopback}\n"
            "commands:\n"
            "  - {id: x.y, handler: a.b:H, transports: [loop], audience: [internal], retries: 2}\n"
        )


def test_retries_with_idempotent_accepted() -> None:
    manifest = _load(
        "transports:\n  - {id: loop, adapter: a.b:C, codec: a.b:D, family: loopback}\n"
        "commands:\n"
        "  - {id: x.y, handler: a.b:H, transports: [loop], audience: [internal], idempotent: true, retries: 2}\n"
    )
    cmd = manifest.commands[0]
    assert cmd.idempotent is True
    assert cmd.retries == 2


def test_manifest_with_no_operations_rejected() -> None:
    with pytest.raises(LoaderError, match="no commands, attributes, or events"):
        _load("transports:\n  - {id: loop, adapter: a.b:C, codec: a.b:D, family: loopback}\ncommands: []\n")


def test_http_block_names_unknown_transport() -> None:
    with pytest.raises(LoaderError, match="http block names transport"):
        _load(
            "transports:\n"
            "  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "  - {id: http2, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "commands:\n"
            "  - id: x.y\n    handler: a.b:H\n    transports: [http]\n    audience: [internal]\n"
            "    http:\n      http: {method: GET, path: /}\n      http2: {method: GET, path: /}\n"
        )


def test_http_block_on_non_http_family() -> None:
    with pytest.raises(LoaderError, match="http block on transport .* but family is"):
        _load(
            "transports:\n  - {id: loop, adapter: a.b:C, codec: a.b:D, family: loopback}\n"
            "commands:\n"
            "  - id: x.y\n    handler: a.b:H\n    transports: [loop]\n    audience: [internal]\n"
            "    http:\n      loop: {method: GET, path: /}\n"
        )


def test_ble_block_names_unknown_transport() -> None:
    with pytest.raises(LoaderError, match="ble block names transport"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {cmd: {write: w, notify: n}}}\n"
            "  - {id: ble2, adapter: a.b:C, codec: a.b:D, family: ble, channels: {cmd: {write: w, notify: n}}}\n"
            "commands:\n"
            "  - id: x.y\n    handler: a.b:H\n    transports: [ble]\n    audience: [internal]\n"
            "    ble:\n      ble: {channel: cmd}\n      ble2: {channel: cmd}\n"
        )


def test_ble_block_on_non_ble_family() -> None:
    with pytest.raises(LoaderError, match="ble block on transport .* but family is"):
        _load(
            "transports:\n  - {id: loop, adapter: a.b:C, codec: a.b:D, family: loopback}\n"
            "commands:\n"
            "  - id: x.y\n    handler: a.b:H\n    transports: [loop]\n    audience: [internal]\n"
            "    ble:\n      loop: {channel: cmd}\n"
        )


def test_ble_block_undeclared_channel() -> None:
    with pytest.raises(LoaderError, match="is not declared in transport"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {cmd: {write: w, notify: n}}}\n"
            "commands:\n"
            "  - id: x.y\n    handler: a.b:H\n    transports: [ble]\n    audience: [internal]\n"
            "    ble:\n      ble: {channel: nonexistent}\n"
        )


def test_http_transport_missing_http_block() -> None:
    with pytest.raises(LoaderError, match="has no http"):
        _load(
            "transports:\n  - {id: http, adapter: a.b:C, codec: a.b:D, family: http}\n"
            "commands:\n  - {id: x.y, handler: a.b:H, transports: [http], audience: [internal]}\n"
        )


def test_ble_transport_missing_ble_block() -> None:
    with pytest.raises(LoaderError, match="has no ble"):
        _load(
            "transports:\n"
            "  - {id: ble, adapter: a.b:C, codec: a.b:D, family: ble, channels: {cmd: {write: w, notify: n}}}\n"
            "commands:\n  - {id: x.y, handler: a.b:H, transports: [ble], audience: [internal]}\n"
        )
