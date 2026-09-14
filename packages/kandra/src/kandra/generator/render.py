"""String templates for the files emitted by `kandra build`.

These functions are intentionally pure: they take simple data (paths,
names, command specs) and return rendered Python source.  Anything that
involves filesystem or import-system side effects lives in
:mod:`kandra.generator.build`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class TransportSpec:
    """Per-transport wiring extracted from the manifest + source tree."""

    transport_id: str  # e.g. "ble" / "http" — value of the TransportId enum member
    enum_member: str  # e.g. "BLE" / "HTTP" — name of the enum member
    family: Literal["loopback", "http", "ble"] | None
    # Codec import is only meaningful for transports without family-paired
    # built-in codecs (loopback / unknown family). HTTP and BLE transports
    # use HttpJsonCodec / BLE codec from kandra_runtime instead.
    codec_import: str | None
    codec_alias: str | None
    # BLE channels declared on this transport: (channel_name, write_uuid, notify_uuid).
    # Empty for non-BLE families. Used by generated client.connect() to wire
    # `BleTransport.from_identity(identity, channels=...)`.
    channels: tuple[tuple[str, str, str], ...] = ()
    # Optional custom transport adapter (dotted path). When None, connect() opens
    # this transport with the runtime default (HttpTransport / BleTransport).
    adapter_import: str | None = None
    adapter_alias: str | None = None


@dataclass(frozen=True)
class HttpCommandWire:
    """HTTP-family wire metadata for a single (command, transport) pair."""

    method: Literal["GET", "POST", "PUT", "DELETE"]
    path: str
    body_codec: Literal["json", "none"]
    response_codec: Literal["json", "none"]
    query_from_request: bool
    expects_response: bool
    timeout: float | None


@dataclass(frozen=True)
class BleCommandWire:
    """BLE-family wire metadata for a single (command, transport) pair."""

    channel: str
    expects_response: bool
    timeout: float | None


@dataclass(frozen=True)
class BleDiscoverySpec:
    """Generator-side BLE discovery criteria (mirrors manifest model)."""

    name_prefix: str | None
    service_uuids: tuple[str, ...]
    manufacturer_id: int | None


@dataclass(frozen=True)
class HttpDiscoverySpec:
    """Generator-side HTTP discovery criteria (mirrors manifest model)."""

    base_urls: tuple[str, ...]
    probe_path: str
    server_header_prefix: str | None


@dataclass(frozen=True)
class DiscoverySpec:
    """Generator-side discovery config — either or both sub-blocks may be set."""

    ble: BleDiscoverySpec | None
    http: HttpDiscoverySpec | None


@dataclass(frozen=True)
class CommandSpec:
    """Per-command wiring extracted from the manifest + source tree."""

    command_id: str  # dotted: "poker.deploy"
    namespace: str  # first segment: "poker"
    method: str  # remaining segments, joined with "_": "deploy"
    timeout: float | None
    request_import: str  # alias import for the request dataclass (for type hints)
    request_alias: str  # e.g. "_Req_poker_deploy"
    response_import: str
    response_alias: str
    transports: list[str]  # transport ids (enum values) this command supports
    http_wires: dict[str, HttpCommandWire] = field(default_factory=dict)
    ble_wires: dict[str, BleCommandWire] = field(default_factory=dict)
    capabilities: tuple[str, ...] = ()  # capability tags gating this op (empty = ungated)
    idempotent: bool = False  # command is safe to auto-resend after a transient failure
    retries: int = 0  # extra transient-failure attempts (honored only when idempotent)


@dataclass(frozen=True)
class SubscribeWire:
    """Per-(primitive, transport) subscribe delivery mode (shared by attributes + events)."""

    enum_member: str  # TransportId member name (e.g. "HTTP")
    mode: Literal["sse", "poll", "ble"]  # native push (sse/ble) or opt-in polling
    interval: float | None  # poll period in seconds; None for push modes


@dataclass(frozen=True)
class AttributeSpec:
    """Facade wiring for one attribute (namespaced read / write / subscribe)."""

    attr_id: str  # dotted: "settings.resolution"
    namespace: str  # first segment: "settings"
    attr_name: str  # remaining segments joined with "_": "resolution"
    class_name: str  # async sub-object class, e.g. "_SettingsResolutionAttribute"
    sync_class_name: str  # sync sub-object class
    value_import: str  # import line for the value type
    value_alias: str  # alias for the value type (read/subscribe payload, write input)
    write_ack_import: str  # import line for the write-ack type
    write_ack_alias: str  # alias for the write-ack type
    operations: tuple[str, ...]  # subset of ("read", "write", "subscribe")
    subscribe_wires: tuple[SubscribeWire, ...]  # empty unless "subscribe" declared


@dataclass(frozen=True)
class EventSpec:
    """Facade wiring for one event (namespaced, subscribe-only, async)."""

    event_id: str  # dotted: "recording.state_changed"
    namespace: str  # first segment: "recording"
    event_name: str  # remaining segments joined with "_": "state_changed"
    class_name: str  # async sub-object class, e.g. "_RecordingStateChangedEvent"
    payload_import: str  # import line for the payload type
    payload_alias: str  # alias for the payload type
    subscribe_wires: tuple[SubscribeWire, ...]  # per-transport delivery modes


def render_init(device_class: str, *, discovery: DiscoverySpec | None = None) -> str:
    """Emit the generated package's ``__init__.py``."""
    sync_class = f"Sync{device_class}"
    extra_imports: list[str] = []
    extra_exports: list[str] = []
    if discovery is not None:
        scanner_exports: list[str] = []
        if discovery.ble is not None:
            scanner_exports.extend(("default_ble_matcher", "make_ble_scanner", "scan_ble"))
        if discovery.http is not None:
            scanner_exports.extend(("default_http_matcher", "make_http_scanner", "scan_http"))
        extra_imports.append(
            f"from {_relative()}.scanners import "
            + ", ".join(scanner_exports)
        )
        extra_exports.extend(scanner_exports)

    extra_import_block = ("\n" + "\n".join(extra_imports)) if extra_imports else ""
    all_list = [device_class, sync_class, "TransportId", *extra_exports]
    all_block = ", ".join(f'"{name}"' for name in all_list)
    return f'''"""Generated SDK. DO NOT EDIT — regenerate with `kandra build`."""

from {_relative()}.client import {device_class}, {sync_class}
from {_relative()}.transports import TransportId{extra_import_block}

__all__ = [{all_block}]
'''


def render_transports(transports: list[TransportSpec]) -> str:
    """Emit the per-device ``TransportId`` enum module."""
    members = "\n".join(
        f'    {t.enum_member} = "{t.transport_id}"' for t in transports
    )
    return f'''"""Transport identifiers declared in the device manifest."""

from __future__ import annotations

from enum import Enum


class TransportId(str, Enum):
    """Wire transports available for this device."""

{members}
'''


def _render_runtime_imports(transports: list[TransportSpec], needs_http_codec: bool) -> str:
    """Compute the ``from kandra_runtime import ...`` block for the registry."""
    lines = ["from kandra_runtime import Command"]
    if needs_http_codec:
        lines.append("from kandra_runtime import HttpJsonCodec, default_http_interpreter")
    if any(t.family == "ble" for t in transports):
        lines.append("from kandra_runtime import BleChannelCodec")
    # BLE and non-http families both use the always-accepted interpreter since
    # BLE has no universal protocol-level status code and loopback/custom
    # families don't either.
    if any(t.family != "http" for t in transports):
        lines.append("from kandra_runtime import always_accepted_interpreter")
    return "\n".join(lines)


def render_registry(commands: list[CommandSpec], transports: list[TransportSpec]) -> str:
    r"""Emit the ``registry.py`` module mapping command ids to per-transport ``Command``\ s."""
    # Collect distinct import lines for user-supplied codecs (non-family
    # transports) and per-command request/response aliases.
    seen: set[str] = set()
    import_lines: list[str] = []
    for t in transports:
        if t.codec_import and t.codec_import not in seen:
            seen.add(t.codec_import)
            import_lines.append(t.codec_import)
    for c in commands:
        for line in (c.request_import, c.response_import):
            if line not in seen:
                seen.add(line)
                import_lines.append(line)

    # Determine if any HTTP-family transports are used (then we need
    # HttpJsonCodec imported from kandra_runtime).
    needs_http_codec = any(t.family == "http" for t in transports)

    entries: list[str] = []
    transport_lookup = {t.transport_id: t for t in transports}
    for c in commands:
        per_transport_lines: list[str] = []
        for tid in c.transports:
            t = transport_lookup[tid]
            per_transport_lines.append(_render_command_entry(c, t))
        entries.append(
            f'    "{c.command_id}": {{\n' + "\n".join(per_transport_lines) + "\n    },"
        )

    runtime_imports = _render_runtime_imports(transports, needs_http_codec)

    imports_block = "\n".join(import_lines)
    entries_block = "\n".join(entries)

    return f'''"""Command registry built from the manifest. DO NOT EDIT."""

from __future__ import annotations

from typing import Any

{runtime_imports}

{imports_block}

from {_relative()}.transports import TransportId

COMMANDS: dict[str, dict[TransportId, Command[Any, Any, Any, Any]]] = {{
{entries_block}
}}
'''


def _render_command_entry(c: CommandSpec, t: TransportSpec) -> str:
    """Render one ``TransportId.X: Command(...)`` line for the registry."""
    indent = "        "
    behavior_kwargs: list[str] = []
    if c.idempotent:
        behavior_kwargs.append("idempotent=True")
    if c.retries:
        behavior_kwargs.append(f"retries={c.retries}")
    behavior = "".join(f"{kw}, " for kw in behavior_kwargs)
    behavior_trailing = "".join(f", {kw}" for kw in behavior_kwargs)
    if t.family == "http":
        wire = c.http_wires[t.transport_id]
        timeout = wire.timeout if wire.timeout is not None else c.timeout
        timeout_arg = f"timeout={timeout}, " if timeout is not None else ""
        expects = "True" if wire.expects_response else "False"
        codec_args = (
            f'method="{wire.method}", path="{wire.path}", '
            f"request_type={c.request_alias}, response_type={c.response_alias}, "
            f"query_from_request={wire.query_from_request}"
        )
        return (
            f"{indent}TransportId.{t.enum_member}: Command(\n"
            f'{indent}    id="{c.command_id}",\n'
            f"{indent}    codec=HttpJsonCodec({codec_args}),\n"
            f"{indent}    interpreter=default_http_interpreter,\n"
            f"{indent}    {timeout_arg}{behavior}expects_response={expects},\n"
            f"{indent}),"
        )
    if t.family == "ble":
        ble_wire = c.ble_wires[t.transport_id]
        timeout = ble_wire.timeout if ble_wire.timeout is not None else c.timeout
        timeout_arg = f"timeout={timeout}, " if timeout is not None else ""
        expects = "True" if ble_wire.expects_response else "False"
        assert t.codec_alias is not None
        # User payload codec is instantiated with (request_type, response_type),
        # then wrapped in BleChannelCodec to attach the per-command channel.
        payload_codec = f"{t.codec_alias}({c.request_alias}, {c.response_alias})"
        return (
            f"{indent}TransportId.{t.enum_member}: Command(\n"
            f'{indent}    id="{c.command_id}",\n'
            f"{indent}    codec=BleChannelCodec(\n"
            f'{indent}        channel="{ble_wire.channel}",\n'
            f"{indent}        payload_codec={payload_codec},\n"
            f"{indent}    ),\n"
            f"{indent}    interpreter=always_accepted_interpreter,\n"
            f"{indent}    {timeout_arg}{behavior}expects_response={expects},\n"
            f"{indent}),"
        )
    # Loopback / unknown family: use the user-supplied codec from the manifest
    # plus the always-accepted interpreter (the user's codec is responsible for
    # raising CodecError on bad data; classification is meaningless here).
    timeout_arg = f", timeout={c.timeout}" if c.timeout is not None else ""
    assert t.codec_alias is not None
    return (
        f"{indent}TransportId.{t.enum_member}: Command(\n"
        f'{indent}    id="{c.command_id}",\n'
        f"{indent}    codec={t.codec_alias}({c.request_alias}, {c.response_alias}),\n"
        f"{indent}    interpreter=always_accepted_interpreter{timeout_arg}{behavior_trailing},\n"
        f"{indent}),"
    )


@dataclass(frozen=True)
class _FacadeSection:
    """Rendered fragments spliced into ``client.py`` for the attribute + event facades."""

    imports: str  # extra top-level runtime imports (NoArgs / dispatch_subscribe / AsyncIterator)
    type_imports: tuple[str, ...]  # value / write-ack / payload alias import lines
    module_level: str  # the ``_SUBSCRIBE_MODES`` delivery-mode table (or "")
    client_methods: str  # the ``_dispatch_subscribe`` method (or "")
    async_classes: str  # attribute + event sub-object classes (async facade)
    sync_classes: str  # attribute sub-object classes (sync facade; events are async-only)
    async_assigns: dict[str, list[str]]  # namespace -> async ``self.<name> = ...`` lines
    sync_assigns: dict[str, list[str]]  # namespace -> sync ``self.<name> = ...`` lines


_EMPTY_FACADE_SECTION = _FacadeSection(
    imports="",
    type_imports=(),
    module_level="",
    client_methods="",
    async_classes="",
    sync_classes="",
    async_assigns={},
    sync_assigns={},
)


def _render_facade_section(
    attributes: tuple[AttributeSpec, ...],
    events: tuple[EventSpec, ...],
    device_class: str,
    subscribe_gate: str = "",
) -> _FacadeSection:
    """Render every fragment the client template needs for the attribute + event facades."""
    if not attributes and not events:
        return _EMPTY_FACADE_SECTION

    # Every event subscribes (so needs NoArgs + the subscribe machinery); attributes
    # need NoArgs for read/subscribe and the machinery only when they subscribe.
    needs_no_args = bool(events) or any("read" in a.operations or "subscribe" in a.operations for a in attributes)
    needs_subscribe = bool(events) or any("subscribe" in a.operations for a in attributes)

    runtime_names = ["NoArgs"] if needs_no_args else []
    if needs_subscribe:
        runtime_names.append("dispatch_subscribe")
    import_lines: list[str] = []
    if runtime_names:
        import_lines.append("from kandra_runtime import " + ", ".join(sorted(runtime_names)))
    if needs_subscribe:
        import_lines.append("from collections.abc import AsyncIterator")

    type_import_set: set[str] = set()
    for a in attributes:
        type_import_set.add(a.value_import)
        if "write" in a.operations:
            type_import_set.add(a.write_ack_import)
    for e in events:
        type_import_set.add(e.payload_import)

    async_classes: list[str] = []
    sync_classes: list[str] = []
    async_assigns: dict[str, list[str]] = {}
    sync_assigns: dict[str, list[str]] = {}
    for a in attributes:
        async_classes.append(_render_attr_class(a, device_class))
        sync_classes.append(_render_sync_attr_class(a, device_class))
        async_assigns.setdefault(a.namespace, []).append(f"        self.{a.attr_name} = {a.class_name}(client)")
        sync_assigns.setdefault(a.namespace, []).append(
            f"        self.{a.attr_name} = {a.sync_class_name}(async_client)"
        )
    for e in events:
        async_classes.append(_render_event_class(e, device_class))
        async_assigns.setdefault(e.namespace, []).append(f"        self.{e.event_name} = {e.class_name}(client)")

    return _FacadeSection(
        imports="\n".join(import_lines),
        type_imports=tuple(sorted(type_import_set)),
        module_level=_render_subscribe_table(attributes, events) if needs_subscribe else "",
        client_methods=_render_dispatch_subscribe(subscribe_gate) if needs_subscribe else "",
        async_classes="\n\n\n".join(async_classes),
        sync_classes="\n\n\n".join(sync_classes),
        async_assigns=async_assigns,
        sync_assigns=sync_assigns,
    )



def _render_attr_class(a: AttributeSpec, device_class: str) -> str:
    """Render the async attribute sub-object exposing read / write / subscribe."""
    methods: list[str] = []
    if "read" in a.operations:
        methods.append(
            f"    async def read(self, *, via: TransportId | None = None) -> Result[{a.value_alias}] | None:\n"
            f'        """Read the current `{a.attr_id}` value."""\n'
            f'        return await self._client._dispatch("{a.attr_id}.read", NoArgs(), via=via)'
        )
    if "write" in a.operations:
        methods.append(
            f"    async def write(\n"
            f"        self, value: {a.value_alias}, *, via: TransportId | None = None\n"
            f"    ) -> Result[{a.write_ack_alias}] | None:\n"
            f'        """Write a new `{a.attr_id}` value."""\n'
            f'        return await self._client._dispatch("{a.attr_id}.write", value, via=via)'
        )
    if "subscribe" in a.operations:
        methods.append(
            f"    def subscribe(\n"
            f"        self, *, via: TransportId | None = None\n"
            f"    ) -> AsyncIterator[Result[{a.value_alias}]]:\n"
            f'        """Stream `{a.attr_id}` updates, one Result per change."""\n'
            f'        return self._client._dispatch_subscribe("{a.attr_id}.subscribe", NoArgs(), via=via)'
        )
    body = "\n\n".join(methods)
    return (
        f"class {a.class_name}:\n"
        f'    """`{a.attr_id}` attribute (read / write / subscribe as declared)."""\n\n'
        f'    def __init__(self, client: "{device_class}") -> None:\n'
        f"        self._client = client\n\n"
        f"{body}"
    )


def _render_sync_attr_class(a: AttributeSpec, device_class: str) -> str:
    """Render the sync attribute sub-object (read / write only; subscribe is async-only)."""
    methods: list[str] = []
    if "read" in a.operations:
        methods.append(
            f"    def read(self, *, via: TransportId | None = None) -> Result[{a.value_alias}] | None:\n"
            f'        """Sync read of `{a.attr_id}`."""\n'
            f'        return asyncio.run(self._async._dispatch("{a.attr_id}.read", NoArgs(), via=via))'
        )
    if "write" in a.operations:
        methods.append(
            f"    def write(\n"
            f"        self, value: {a.value_alias}, *, via: TransportId | None = None\n"
            f"    ) -> Result[{a.write_ack_alias}] | None:\n"
            f'        """Sync write of `{a.attr_id}`."""\n'
            f'        return asyncio.run(self._async._dispatch("{a.attr_id}.write", value, via=via))'
        )
    header = (
        f"class {a.sync_class_name}:\n"
        f'    """`{a.attr_id}` attribute (sync; read / write only)."""\n\n'
        f'    def __init__(self, async_client: "{device_class}") -> None:\n'
        f"        self._async = async_client"
    )
    if not methods:
        return header
    return header + "\n\n" + "\n\n".join(methods)


def _render_event_class(e: EventSpec, device_class: str) -> str:
    """Render the async event sub-object exposing a single subscribe() stream."""
    return (
        f"class {e.class_name}:\n"
        f'    """`{e.event_id}` event (subscribe-only stream)."""\n\n'
        f'    def __init__(self, client: "{device_class}") -> None:\n'
        f"        self._client = client\n\n"
        f"    def subscribe(\n"
        f"        self, *, via: TransportId | None = None\n"
        f"    ) -> AsyncIterator[Result[{e.payload_alias}]]:\n"
        f'        """Stream `{e.event_id}` emissions, one Result per event."""\n'
        f'        return self._client._dispatch_subscribe("{e.event_id}.subscribe", NoArgs(), via=via)'
    )


def _render_subscribe_entry(op_id: str, wires: tuple[SubscribeWire, ...]) -> str:
    """Render one ``"<id>.subscribe": {TransportId.X: (mode, interval), ...}`` table block."""
    wire_lines = [f'        TransportId.{w.enum_member}: ("{w.mode}", {w.interval!r}),' for w in wires]
    return f'    "{op_id}.subscribe": {{\n' + "\n".join(wire_lines) + "\n    },"


def _render_subscribe_table(attributes: tuple[AttributeSpec, ...], events: tuple[EventSpec, ...]) -> str:
    """Render the ``_SUBSCRIBE_MODES`` map: subscribe op id -> transport -> (mode, interval)."""
    entries = [
        _render_subscribe_entry(a.attr_id, a.subscribe_wires) for a in attributes if "subscribe" in a.operations
    ]
    entries += [_render_subscribe_entry(e.event_id, e.subscribe_wires) for e in events]
    return "_SUBSCRIBE_MODES: dict[str, dict[TransportId, tuple[str, float | None]]] = {\n" + "\n".join(entries) + "\n}"



# Spliced verbatim into the client class when any attribute *or* event subscribes.
# Braces are intentionally literal: this string is substituted as a value, not a template.
def _render_dispatch_subscribe(capability_gate: str) -> str:
    """Render the client ``_dispatch_subscribe`` method, with an optional capability gate."""
    return f'''
    def _dispatch_subscribe(
        self,
        command_id: str,
        request: Any,
        *,
        via: TransportId | None,
    ) -> "AsyncIterator[Result[Any]]":
        """Internal: stream Results for an attribute/event subscribe (native push or opt-in poll)."""
{capability_gate}
        async def _stream() -> "AsyncIterator[Result[Any]]":
            chosen = self._resolve_transport(command_id, via)
            command = COMMANDS[command_id][chosen]
            transport = self._transports[chosen]
            mode, interval = _SUBSCRIBE_MODES[command_id][chosen]
            if mode == "poll":
                while True:
                    result = await dispatch(command, transport, request)
                    if result is not None:
                        self._fire_hook(result)
                        yield result
                    await asyncio.sleep(interval if interval is not None else 0.0)
            else:
                async for result in dispatch_subscribe(command, transport, request):
                    self._fire_hook(result)
                    yield result

        return _stream()'''


def _render_command_method(c: CommandSpec, *, sync: bool) -> str:
    """Render one namespace command method (async coroutine or sync ``asyncio.run`` wrapper)."""
    if sync:
        return (
            f"    def {c.method}(\n"
            f"        self,\n"
            f"        request: {c.request_alias},\n"
            f"        *,\n"
            f"        via: TransportId | None = None,\n"
            f"    ) -> Result[{c.response_alias}] | None:\n"
            f'        """Sync wrapper for `{c.command_id}`."""\n'
            f"        return asyncio.run(\n"
            f'            self._async._dispatch("{c.command_id}", request, via=via)\n'
            f"        )"
        )
    return (
        f"    async def {c.method}(\n"
        f"        self,\n"
        f"        request: {c.request_alias},\n"
        f"        *,\n"
        f"        via: TransportId | None = None,\n"
        f"    ) -> Result[{c.response_alias}] | None:\n"
        f'        """Invoke `{c.command_id}`.\n\n'
        f"        Returns a :class:`Result` envelope wrapping the typed response,\n"
        f"        or ``None`` when this transport's spec sets\n"
        f'        ``expects_response: false`` (fire-and-forget)."""\n'
        f'        return await self._client._dispatch("{c.command_id}", request, via=via)'
    )


def _render_namespace_class(
    ns: str, *, sync: bool, device_class: str, methods: list[str], assigns: list[str]
) -> str:
    """Render one namespace class (async or sync) with its command methods + sub-object assigns."""
    if sync:
        header = (
            f"class _Sync{_pascal(ns)}Namespace:\n"
            f'    """`{ns}.*` sync operations."""\n\n'
            f"    def __init__(self, async_client: {device_class}) -> None:\n"
        )
        init = "        self._async = async_client"
    else:
        header = (
            f"class _{_pascal(ns)}Namespace:\n"
            f'    """`{ns}.*` async operations."""\n\n'
            f"    def __init__(self, client: {device_class}) -> None:\n"
        )
        init = "        self._client = client"
    if assigns:
        init += "\n" + "\n".join(assigns)
    methods_block = "\n\n".join(methods)
    body = init + (f"\n\n{methods_block}" if methods_block else "")
    return header + body


@dataclass(frozen=True)
class _CapabilitySection:
    """Rendered fragments spliced into ``client.py`` for capability negotiation."""

    imports: str  # runtime capability imports (empty when no op is gated)
    module_level: str  # the ``_CAPABILITIES`` table
    init_line: str  # ``self._capabilities`` init line (trailing newline) or ""
    client_methods: str  # discover_capabilities + _check_capability (async client)
    sync_methods: str  # sync discover_capabilities wrapper
    dispatch_gate: str  # gate line spliced into ``_dispatch`` (trailing newline) or ""
    subscribe_gate: str  # gate line spliced into ``_dispatch_subscribe`` (no newline) or ""


_EMPTY_CAPABILITY_SECTION = _CapabilitySection("", "", "", "", "", "", "")


_CAPABILITY_CLIENT_METHODS = '''
    async def discover_capabilities(self, probe: CapabilityProbe) -> Capabilities:
        """Query the device via ``probe`` and cache which operations are available.

        Until this is called, no operation is gated. Afterwards, calling an
        operation whose required capability tags the device lacks raises
        ``CapabilityUnavailableError`` locally instead of failing on the wire.
        """
        self._capabilities = Capabilities(await probe.probe(self))
        return self._capabilities

    def _check_capability(self, command_id: str) -> None:
        """Raise if capabilities were discovered and ``command_id`` is unsupported."""
        caps = self._capabilities
        if caps is None:
            return
        required = _CAPABILITIES.get(command_id)
        if required is not None and not caps.supports(required):
            missing = tuple(t for t in required if t not in caps.supported)
            raise CapabilityUnavailableError(command_id, missing)'''


_CAPABILITY_SYNC_METHOD = '''
    def discover_capabilities(self, probe: CapabilityProbe) -> Capabilities:
        """Sync wrapper: query the device and cache which operations are available."""
        return asyncio.run(self._async.discover_capabilities(probe))'''


def _render_tag_tuple(tags: tuple[str, ...]) -> str:
    """Render a tag tuple as a Python literal (trailing comma keeps 1-tuples valid)."""
    inner = ", ".join(f'"{t}"' for t in tags)
    return f"({inner},)"


def _render_capability_section(capability_map: dict[str, tuple[str, ...]]) -> _CapabilitySection:
    """Render the ``_CAPABILITIES`` table, discover method, and dispatch gates.

    Returns the empty section when no operation declares capability tags, so a
    manifest that never gates anything produces a byte-identical command-only client.
    """
    if not capability_map:
        return _EMPTY_CAPABILITY_SECTION
    entries = [f'    "{op_id}": {_render_tag_tuple(tags)},' for op_id, tags in capability_map.items()]
    module_level = "_CAPABILITIES: dict[str, tuple[str, ...]] = {\n" + "\n".join(entries) + "\n}"
    return _CapabilitySection(
        imports="from kandra_runtime import Capabilities, CapabilityProbe, CapabilityUnavailableError",
        module_level=module_level,
        init_line="        self._capabilities: Capabilities | None = None\n",
        client_methods=_CAPABILITY_CLIENT_METHODS,
        sync_methods=_CAPABILITY_SYNC_METHOD,
        dispatch_gate="        self._check_capability(command_id)\n",
        subscribe_gate="        self._check_capability(command_id)",
    )


def render_client(
    device_class: str,
    commands: list[CommandSpec],
    *,
    device_id: str,
    transports: list[TransportSpec],
    discovery: DiscoverySpec | None = None,
    attributes: tuple[AttributeSpec, ...] = (),
    events: tuple[EventSpec, ...] = (),
    capabilities: dict[str, tuple[str, ...]] | None = None,
) -> str:
    """Emit the user-facing async client facade plus a sync wrapper."""
    capability_section = _render_capability_section(capabilities or {})
    facade_section = _render_facade_section(attributes, events, device_class, capability_section.subscribe_gate)

    commands_by_ns: dict[str, list[CommandSpec]] = {}
    for c in commands:
        commands_by_ns.setdefault(c.namespace, []).append(c)

    # The async facade groups commands + attributes + events; the sync facade
    # omits events (subscribe is async-only) and so may skip event-only namespaces.
    async_ns_order = list(commands_by_ns)
    for ns in [a.namespace for a in attributes] + [e.namespace for e in events]:
        if ns not in async_ns_order:
            async_ns_order.append(ns)
    sync_ns_order = list(commands_by_ns)
    for a in attributes:
        if a.namespace not in sync_ns_order:
            sync_ns_order.append(a.namespace)

    sync_class = f"Sync{device_class}"
    namespace_classes = [
        _render_namespace_class(
            ns,
            sync=False,
            device_class=device_class,
            methods=[_render_command_method(c, sync=False) for c in commands_by_ns.get(ns, [])],
            assigns=facade_section.async_assigns.get(ns, []),
        )
        for ns in async_ns_order
    ]
    namespace_assigns = [f"        self.{ns} = _{_pascal(ns)}Namespace(self)" for ns in async_ns_order]
    sync_namespace_classes = [
        _render_namespace_class(
            ns,
            sync=True,
            device_class=device_class,
            methods=[_render_command_method(c, sync=True) for c in commands_by_ns.get(ns, [])],
            assigns=facade_section.sync_assigns.get(ns, []),
        )
        for ns in sync_ns_order
    ]
    sync_namespace_assigns = [f"        self.{ns} = _Sync{_pascal(ns)}Namespace(self._async)" for ns in sync_ns_order]

    type_alias_imports = "\n".join(
        sorted(
            {c.request_import for c in commands}
            | {c.response_import for c in commands}
            | set(facade_section.type_imports)
        )
    )
    namespace_classes_block = "\n\n\n".join(namespace_classes)
    sync_namespace_classes_block = "\n\n\n".join(sync_namespace_classes)
    namespace_assigns_block = "\n".join(namespace_assigns)
    sync_namespace_assigns_block = "\n".join(sync_namespace_assigns)

    facade_class_parts = [p for p in (facade_section.async_classes, facade_section.sync_classes) if p]
    block_sep = "\n\n\n"
    facade_classes_block = f"{block_sep}{block_sep.join(facade_class_parts)}" if facade_class_parts else ""

    connect_section = _render_connect_section(device_id, transports, discovery=discovery)

    return f'''"""Generated client facade. DO NOT EDIT — regenerate with `kandra build`."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from types import TracebackType
from typing import Any

from kandra_runtime import Command, IdentityStaleError, Result, Transport, dispatch, format_failure
{connect_section.imports}
{facade_section.imports}
{capability_section.imports}

{type_alias_imports}

from {_relative()}.registry import COMMANDS
from {_relative()}.transports import TransportId

{connect_section.module_level}
{facade_section.module_level}
{capability_section.module_level}

class {device_class}:
    """Generated async facade for the device.

    Construct with a mapping of :class:`TransportId` to live
    :class:`kandra_runtime.Transport` instances; operations are exposed as
    ``client.<namespace>.<method>(request, *, via=...)`` coroutines.

    Every operation returns a :class:`Result` envelope. The library
    default is to *not* fail-fast on non-ACCEPTED results -- the caller
    inspects ``result.accepted`` (or branches on
    ``result.classification``) and reads ``result.data`` only when
    accepted. A test framework can opt into fail-fast by assigning
    ``client.on_non_accepted = test_framework.fail_test``; the hook
    receives a single human-readable string built by
    :func:`format_failure`. Wrap calls in :meth:`ignore_failures` to
    temporarily suppress the hook for negative-path tests.

    The client may be used as an async context manager. Transports
    constructed by :meth:`connect` are closed on exit; transports
    passed in via the constructor are left untouched (their lifecycle
    belongs to the caller).
    """

    def __init__(self, *, transports: Mapping[TransportId, Transport[Any, Any]]) -> None:
        """Wire the client to one or more transport instances."""
        if not transports:
            raise ValueError("at least one transport must be provided")
        self._transports: dict[TransportId, Transport[Any, Any]] = dict(transports)
        self._owned_transports: dict[TransportId, Transport[Any, Any]] = {{}}
        self.on_non_accepted: Any = None
        """Optional callback ``(failure_summary: str) -> None`` invoked on non-ACCEPTED results.

        ``None`` by default (library policy: surface the Result and let
        the caller decide). Test frameworks wire this to their
        ``fail_test`` entry point.
        """
        self._suppress_hook = False
        self._mid_session_refresh: Callable[[], Awaitable[None]] | None = None
        """Async recovery hook installed by :meth:`connect` when
        ``refresh_mid_session=True``: re-enrolls + rebuilds transports in place
        after a mid-session :class:`IdentityStaleError`. ``None`` disables it."""
{capability_section.init_line}{namespace_assigns_block}

{connect_section.client_methods}

    async def __aenter__(self) -> "{device_class}":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close every transport this client owns (built by :meth:`connect`).

        Transports supplied via the constructor are *not* closed — the
        caller's own ``async with`` owns those.
        """
        for transport in list(self._owned_transports.values()):
            with contextlib.suppress(Exception):
                await transport.close()
        self._owned_transports.clear()

    @contextmanager
    def ignore_failures(self) -> Iterator[None]:
        """Suppress :attr:`on_non_accepted` for commands run inside the block.

        The :class:`Result` is still returned to the caller; only the
        framework-installed hook is bypassed. Nesting is supported.
        """
        previous = self._suppress_hook
        self._suppress_hook = True
        try:
            yield
        finally:
            self._suppress_hook = previous

    def _resolve_transport(self, command_id: str, via: TransportId | None) -> TransportId:
        """Pick the transport for an operation: explicit ``via`` or first wired one."""
        per_transport = COMMANDS[command_id]
        if via is not None:
            if via not in per_transport:
                raise ValueError(
                    f"command {{command_id!r}} does not support transport {{via.value!r}}"
                )
            if via not in self._transports:
                raise ValueError(f"transport {{via.value!r}} is not wired into this client")
            return via
        chosen = next((t for t in per_transport if t in self._transports), None)
        if chosen is None:
            raise ValueError(f"no wired transport supports command {{command_id!r}}")
        return chosen

    def _fire_hook(self, result: Result[Any] | None) -> None:
        """Fire ``on_non_accepted`` for a non-ACCEPTED result when the hook is armed."""
        if (
            result is not None
            and not result.accepted
            and self.on_non_accepted is not None
            and not self._suppress_hook
        ):
            self.on_non_accepted(format_failure(result))

    async def _dispatch(
        self,
        command_id: str,
        request: Any,
        *,
        via: TransportId | None,
    ) -> Result[Any] | None:
        """Internal: resolve the transport, run the command, fire the hook if armed.

        If a mid-session refresh hook is installed (``connect(...,
        refresh_mid_session=True)``) and the transport raises
        :class:`IdentityStaleError`, the hook re-enrolls + rebuilds transports
        in place and the command is retried exactly once.
        """
{capability_section.dispatch_gate}        chosen = self._resolve_transport(command_id, via)
        command = COMMANDS[command_id][chosen]
        try:
            result = await dispatch(command, self._transports[chosen], request)
        except IdentityStaleError:
            if self._mid_session_refresh is None:
                raise
            await self._mid_session_refresh()
            chosen = self._resolve_transport(command_id, via)
            command = COMMANDS[command_id][chosen]
            result = await dispatch(command, self._transports[chosen], request)
        self._fire_hook(result)
        return result
{facade_section.client_methods}
{capability_section.client_methods}


class {sync_class}:
    """Generated **sync** facade. Wraps the async client with `asyncio.run`.

    Must not be called from inside a running event loop. Use the async
    `{device_class}` when one is already running. The async client is
    accessible as :attr:`async_client` for direct hook configuration
    (``client.async_client.on_non_accepted = ...``).
    """

    def __init__(self, *, transports: Mapping[TransportId, Transport[Any, Any]]) -> None:
        self._async = {device_class}(transports=transports)
{sync_namespace_assigns_block}

    @property
    def async_client(self) -> {device_class}:
        """The underlying async client (for hook configuration, etc)."""
        return self._async
{capability_section.sync_methods}


{namespace_classes_block}


{sync_namespace_classes_block}{facade_classes_block}
'''


@dataclass(frozen=True)
class _ConnectSection:
    """Three rendered blocks spliced into ``client.py`` for the connect feature."""

    imports: str  # extra top-level imports
    module_level: str  # module-level constants + helpers (channel maps, factories)
    client_methods: str  # methods added to the device class


def _render_discover_and_connect(families: list[str]) -> str:
    """Render ``_enroll_and_save`` (shared), ``re_enroll``, and ``discover_and_connect``.

    ``families`` lists the manifest families that have *both* a
    transport entry and a discovery block — those are the families
    eligible for scan + enroll.
    """
    # Per-family scan+enroll snippet (executed inside the method).
    scan_blocks: list[str] = []
    for fam in families:
        scan_blocks.append(
            f'        enrollment_for_{fam} = enrollment_map.get("{fam}")\n'
            f"        if enrollment_for_{fam} is not None:\n"
            f"            candidates = await scan_{fam}(timeout=discovery_timeout)\n"
            f"            if not candidates:\n"
            f"                raise EnrollmentError(\n"
            f'                    f"enroll: no {fam.upper()} candidates found "\n'
            f'                    f"within {{discovery_timeout}}s"\n'
            f"                )\n"
            f"            sub_identity = await enrollment_for_{fam}.enroll(\n"
            f"                candidates[0], saved_name=saved_name\n"
            f"            )\n"
            f'            collected["{fam}"] = sub_identity'
        )
    scan_blocks_joined = "\n".join(scan_blocks)
    known_families_repr = ", ".join(repr(f) for f in families)

    return f'''

    @classmethod
    async def _enroll_and_save(
        cls,
        saved_name: str,
        *,
        enrollment: "Enrollment | Mapping[str, Enrollment]",
        store: "IdentityStore",
        discovery_timeout: float,
    ) -> "Identity":
        """Scan every discoverable family, enroll, and persist the (possibly composite) identity."""
        known_families: tuple[str, ...] = ({known_families_repr},)
        if isinstance(enrollment, Mapping):
            enrollment_map: dict[str, Enrollment] = {{
                fam: adapter for fam, adapter in enrollment.items()
                if fam in known_families
            }}
        else:
            if len(known_families) != 1:
                raise ValueError(
                    "enroll: this device has multiple discoverable families "
                    f"({{known_families}}); pass enrollment as a mapping keyed by "
                    "family, not a single Enrollment instance"
                )
            enrollment_map = {{known_families[0]: enrollment}}

        if not enrollment_map:
            raise ValueError(
                "enroll: no enrollment adapter matches this device's "
                f"discoverable families {{known_families}}"
            )

        collected: dict[str, Identity] = {{}}
{scan_blocks_joined}

        if len(collected) == 1:
            (only_identity,) = collected.values()
            identity_to_save: Identity = only_identity
        else:
            identity_to_save = CompositeIdentity(
                saved_name=saved_name,
                components=dict(collected),
            )
        store.save(identity_to_save)
        return identity_to_save

    @classmethod
    async def re_enroll(
        cls,
        saved_name: str,
        *,
        enrollment: "Enrollment | Mapping[str, Enrollment]",
        store: "IdentityStore | None" = None,
        discovery_timeout: float = 10.0,
    ) -> "Identity":
        """Re-run enrollment for a known device and atomically overwrite its saved record.

        Use when stored credentials have gone stale (see
        :class:`~kandra_runtime.IdentityStaleError`): re-discovers the device via
        the manifest ``scan_<family>`` helpers, runs ``enrollment`` again, and
        replaces the ``saved_name`` record. Returns the fresh identity.

        Typically wired into :meth:`connect` as the recovery hook::

            await Client.connect(
                saved_name,
                on_stale=lambda name: Client.re_enroll(name, enrollment=my_enrollment),
            )
        """
        if store is None:
            store = PlatformDirsJsonStore(app_name=_DEFAULT_APP_NAME)
        return await cls._enroll_and_save(
            saved_name, enrollment=enrollment, store=store, discovery_timeout=discovery_timeout
        )

    @classmethod
    async def discover_and_connect(
        cls,
        saved_name: str,
        *,
        enrollment: "Enrollment | Mapping[str, Enrollment]",
        store: "IdentityStore | None" = None,
        discovery_timeout: float = 10.0,
        transports: "Collection[TransportId] | None" = None,
    ) -> "Any":
        """One-shot connect: load saved identity, or scan + enroll + save on first run.

        On the first call for a new ``saved_name`` this method runs
        the full setup pipeline:

        1. Discover candidate devices using the manifest-generated
           ``scan_<family>()`` helpers (one per discoverable family).
        2. Run the supplied :class:`Enrollment` against the first
           match in each family, producing one or more sub-identities.
        3. Save the resulting (possibly :class:`CompositeIdentity`)
           record into ``store``.
        4. Delegate to :meth:`connect` to open every transport.

        On subsequent calls — the typical "second run" case — the
        saved identity is loaded straight from ``store`` and only
        :meth:`connect` is invoked; no scanning or enrollment happens.

        Parameters
        ----------
        saved_name:
            Friendly name to look up (or, on first run, to store under).
        enrollment:
            Either a single :class:`Enrollment` (acceptable when this
            device has exactly one discoverable family) or a mapping
            of family name (``"ble"`` / ``"http"``) to its
            :class:`Enrollment` adapter. Only the entries matching
            this device's discoverable families ({known_families_repr})
            are consulted.
        store:
            Identity store. Defaults to a
            :class:`PlatformDirsJsonStore` keyed on this device's id.
        discovery_timeout:
            Per-family scan window in seconds. Default 10.0.
        transports:
            Optional filter passed through to :meth:`connect`.

        Raises
        ------
        EnrollmentError:
            A discoverable family had zero scan matches, or the
            supplied :class:`Enrollment` rejected the candidate.
        ValueError:
            ``enrollment`` is a single adapter but this device has
            multiple discoverable families (ambiguous wiring).

        Notes
        -----
        This method takes the *first* candidate returned by each
        scanner. Devices in noisy environments (multiple cameras on
        the bench) should call :func:`scan_ble` / :func:`scan_http`
        directly, pick the one they want, then run the explicit
        :class:`Enrollment` + :meth:`connect` flow.
        """
        if store is None:
            store = PlatformDirsJsonStore(app_name=_DEFAULT_APP_NAME)

        # Fast path: already enrolled.
        try:
            return await cls.connect(saved_name, store=store, transports=transports)
        except IdentityNotFoundError:
            pass

        await cls._enroll_and_save(
            saved_name, enrollment=enrollment, store=store, discovery_timeout=discovery_timeout
        )
        return await cls.connect(saved_name, store=store, transports=transports)'''


def _render_connect_section(  # noqa: C901  (branch-heavy code generator)
    device_id: str,
    transports: list[TransportSpec],
    *,
    discovery: DiscoverySpec | None = None,
) -> _ConnectSection:
    """Render the ``connect()`` / ``list_saved()`` / ``discover_and_connect()`` plumbing.

    The generator emits a per-BLE-transport ``_BLE_CHANNELS_<id>`` constant
    (channels are manifest-defined, not part of the saved identity), a
    ``_TRANSPORT_FACTORIES`` registry mapping each manifest transport to a
    ``from_identity`` callable + its family, and a ``_identity_for_family``
    helper that picks the right sub-record out of a ``CompositeIdentity``.

    When ``discovery`` is supplied, a third classmethod
    ``discover_and_connect()`` is appended that hides the
    scan → enroll → save → connect dance behind a single call.
    """
    has_ble = any(t.family == "ble" for t in transports)
    has_http = any(t.family == "http" for t in transports)
    if not (has_ble or has_http):
        # No persistent-identity transports → no connect() codegen.
        empty = ""
        return _ConnectSection(imports=empty, module_level=empty, client_methods=empty)

    # Families with both a manifest transport AND a discovery scanner.
    discoverable_families: list[str] = []
    if discovery is not None:
        if has_ble and discovery.ble is not None:
            discoverable_families.append("ble")
        if has_http and discovery.http is not None:
            discoverable_families.append("http")

    # Runtime transports are imported only for families whose transports use the
    # default (adapter omitted); a custom adapter is imported + used instead.
    needs_ble_runtime = any(t.family == "ble" and t.adapter_alias is None for t in transports)
    needs_http_runtime = any(t.family == "http" and t.adapter_alias is None for t in transports)
    runtime_extra_imports: list[str] = ["BleIdentity", "CompositeIdentity", "HttpIdentity"]
    if needs_ble_runtime:
        runtime_extra_imports.append("BleTransport")
    if needs_http_runtime:
        runtime_extra_imports.append("HttpTransport")
    runtime_extra_imports.extend(
        ["Identity", "IdentityStore", "PlatformDirsJsonStore"]
    )
    if discoverable_families:
        runtime_extra_imports.extend(
            ["Enrollment", "EnrollmentError", "IdentityNotFoundError"]
        )
    imports_block = (
        "from kandra_runtime import (\n"
        + "".join(f"    {name},\n" for name in sorted(set(runtime_extra_imports)))
        + ")"
    )
    if discoverable_families:
        scanner_imports = ", ".join(f"scan_{fam}" for fam in discoverable_families)
        imports_block += f"\nfrom .scanners import {scanner_imports}"
    # datetime powers connect()'s last_validated stamp; Awaitable/Callable come from client.py's core imports.
    imports_block += "\nfrom datetime import UTC, datetime"

    # Per-BLE-transport channel maps + factory entries.
    module_lines: list[str] = [f'_DEFAULT_APP_NAME = "{device_id}_sdk"']
    factory_entries: list[str] = []
    adapter_imports: list[str] = []
    for t in transports:
        if t.adapter_import is not None:
            adapter_imports.append(t.adapter_import)
        if t.family == "ble":
            chan_var = f"_BLE_CHANNELS_{_sanitize(t.transport_id)}"
            chan_items = ",\n".join(
                f'    "{name}": ("{w}", "{n}")' for (name, w, n) in t.channels
            )
            module_lines.append(
                f"{chan_var}: dict[str, tuple[str, str]] = {{\n{chan_items},\n}}"
            )
            factory_entries.append(
                f'    (TransportId.{t.enum_member}, "ble", '
                f"lambda ident: {t.adapter_alias or 'BleTransport'}.from_identity(ident, channels={chan_var})),"
            )
        elif t.family == "http":
            factory_entries.append(
                f'    (TransportId.{t.enum_member}, "http", '
                f"lambda ident: {t.adapter_alias or 'HttpTransport'}.from_identity(ident)),"
            )
        # Loopback / unknown families: no from_identity available; skip.

    if adapter_imports:
        imports_block += "\n" + "\n".join(adapter_imports)
    imports = imports_block

    factories_block = (
        "_TRANSPORT_FACTORIES: tuple[\n"
        '    tuple[TransportId, str, "Any"], ...\n'
        "] = (\n" + "\n".join(factory_entries) + "\n)"
    )

    identity_helper = '''def _identity_for_family(identity: "Identity", family: str) -> "Any":
    """Return the sub-identity matching ``family`` or ``None`` if absent.

    Plain ``BleIdentity`` / ``HttpIdentity`` match their own family
    directly; a :class:`CompositeIdentity` is searched by walking its
    ``components`` map for the first sub-identity whose ``transport``
    literal equals ``family``.
    """
    if family == "ble" and isinstance(identity, BleIdentity):
        return identity
    if family == "http" and isinstance(identity, HttpIdentity):
        return identity
    if isinstance(identity, CompositeIdentity):
        for sub in identity.components.values():
            if getattr(sub, "transport", None) == family:
                return sub
    return None'''

    mark_validated_helper = '''def _mark_validated(store: "IdentityStore", identity: "Identity") -> None:
    """Best-effort: stamp ``last_validated=now`` on a working identity and persist it.

    Called after a successful connect so a store can surface "credentials last
    confirmed working". Failures are swallowed -- a store write must never break
    an otherwise-live connection.
    """
    with contextlib.suppress(Exception):
        store.save(identity.model_copy(update={"last_validated": datetime.now(UTC)}))'''

    mid_session_helper = '''def _make_mid_session_refresh(
    client: "Any",
    cls: "Any",
    store: "IdentityStore",
    saved_name: str,
    on_stale: "Callable[[str], Awaitable[Identity]]",
    filter_ids: "set[TransportId] | None",
) -> "Callable[[], Awaitable[None]]":
    """Build the mid-session recovery hook ``connect`` installs on a client.

    The returned coroutine re-enrolls via ``on_stale``, opens a fresh transport
    set, swaps it into the live ``client``, closes the superseded transports,
    and re-stamps ``last_validated``. A second :class:`IdentityStaleError` from
    the reopen propagates to the awaiting command (a single retry only).
    """
    async def _refresh() -> None:
        identity = await on_stale(saved_name)
        rebuilt = await cls._open_transports(identity, filter_ids)
        superseded = dict(client._owned_transports)
        client._transports = dict(rebuilt)
        client._owned_transports = rebuilt
        for transport in superseded.values():
            with contextlib.suppress(Exception):
                await transport.close()
        _mark_validated(store, identity)

    return _refresh'''

    module_level = "\n\n".join(
        [
            *module_lines,
            factories_block,
            identity_helper,
            mark_validated_helper,
            mid_session_helper,
        ]
    )

    client_methods = '''    @classmethod
    async def _open_transports(
        cls,
        identity: "Identity",
        filter_ids: "set[TransportId] | None",
    ) -> "dict[TransportId, Transport[Any, Any]]":
        """Build + open every transport the identity supports, closing partials on failure."""
        built: dict[TransportId, Transport[Any, Any]] = {}
        try:
            for tid, family, factory in _TRANSPORT_FACTORIES:
                if filter_ids is not None and tid not in filter_ids:
                    continue
                sub_identity = _identity_for_family(identity, family)
                if sub_identity is None:
                    continue
                transport = factory(sub_identity)
                await transport.open()
                built[tid] = transport
        except BaseException:
            for opened in built.values():
                with contextlib.suppress(Exception):
                    await opened.close()
            raise
        return built

    @classmethod
    async def connect(
        cls,
        saved_name: str,
        *,
        store: "IdentityStore | None" = None,
        transports: "Collection[TransportId] | None" = None,
        on_stale: "Callable[[str], Awaitable[Identity]] | None" = None,
        refresh_mid_session: bool = False,
    ) -> "Any":
        """Build and open a client from a previously enrolled identity.

        Looks up ``saved_name`` in ``store`` (defaulting to a
        :class:`~kandra_runtime.PlatformDirsJsonStore` keyed on this
        device's id), instantiates every manifest transport whose
        family the saved identity supports, opens each one, and
        returns a ready-to-use client.

        Parameters
        ----------
        saved_name:
            Friendly name passed to a prior ``enroll()`` call.
        store:
            Identity store to query. Defaults to a
            :class:`PlatformDirsJsonStore` with
            ``app_name=f\"{device_id}_sdk\"``.
        transports:
            Optional filter — restrict activation to this set of
            transport ids. If omitted, every transport the saved
            identity supplies is activated.
        on_stale:
            Optional async ``(saved_name) -> Identity`` recovery callback.
            When a transport's ``open()`` raises :class:`IdentityStaleError`
            (e.g. a stale BLE bond), it is invoked to re-establish
            credentials (typically ``re_enroll``) and the connect is retried
            once. When ``None`` (default), the stale error propagates.
        refresh_mid_session:
            When ``True``, also install ``on_stale`` as a *mid-session*
            recovery hook: if a later command's :func:`dispatch` raises
            :class:`IdentityStaleError` (e.g. an HTTP token expiring after
            connect), the client re-enrolls, rebuilds its transports in
            place, and retries that command exactly once. Off by default so a
            connectivity/auth test harness observes the raw
            :class:`IdentityStaleError`. Requires ``on_stale``.

        Raises
        ------
        IdentityNotFoundError:
            ``saved_name`` is not present in the store.
        IdentityStaleError:
            Credentials were rejected at ``open()`` and no ``on_stale``
            recovery callback was supplied (or recovery failed again).
        ValueError:
            ``refresh_mid_session=True`` was passed without ``on_stale``, or
            the saved identity supplied no usable transport (e.g. it stores
            only HTTP credentials but ``transports={TransportId.BLE}`` was
            requested).
        TransportError:
            A transport's ``open()`` call failed; all transports opened
            so far in this call are closed before re-raising.
        """
        if store is None:
            store = PlatformDirsJsonStore(app_name=_DEFAULT_APP_NAME)
        if refresh_mid_session and on_stale is None:
            raise ValueError(
                "connect: refresh_mid_session=True requires an on_stale callback"
            )
        identity = store.load(saved_name)
        filter_ids: "set[TransportId] | None" = (
            None if transports is None else set(transports)
        )
        try:
            built = await cls._open_transports(identity, filter_ids)
        except IdentityStaleError:
            if on_stale is None:
                raise
            # Stored credentials rejected at open(); recover once via on_stale.
            identity = await on_stale(saved_name)
            built = await cls._open_transports(identity, filter_ids)

        if not built:
            raise ValueError(
                f"saved identity {saved_name!r} supplied no transports "
                f"matching this device's manifest"
            )

        _mark_validated(store, identity)
        client = cls(transports=built)
        client._owned_transports = built
        if refresh_mid_session and on_stale is not None:
            client._mid_session_refresh = _make_mid_session_refresh(
                client, cls, store, saved_name, on_stale, filter_ids
            )
        return client

    @classmethod
    def list_saved(cls, *, store: "IdentityStore | None" = None) -> list[str]:
        """Return the saved names known to ``store`` (default platformdirs store).

        Symmetric with :meth:`connect`: every name returned here is a
        valid argument to ``connect(saved_name=...)``.
        """
        if store is None:
            store = PlatformDirsJsonStore(app_name=_DEFAULT_APP_NAME)
        return [ident.saved_name for ident in store.list_saved()]'''

    if discoverable_families:
        client_methods += _render_discover_and_connect(discoverable_families)

    return _ConnectSection(
        imports=imports,
        module_level=module_level,
        client_methods=client_methods,
    )


def render_scanners(discovery: DiscoverySpec) -> str:
    """Emit ``scanners.py`` with default matchers + scanner factories.

    Generated only when the manifest declares a ``discovery:`` block.
    Each present family (``ble`` / ``http``) contributes:

    * ``default_<family>_matcher(candidate)`` — AND of the criteria
      declared in the manifest.
    * ``make_<family>_scanner()`` — returns a ready-to-use
      :class:`kandra_runtime.Scanner` (HTTP scanner is pre-configured
      with ``base_urls`` / ``probe_path`` from the manifest).
    * ``scan_<family>(*, timeout, matcher=default_<family>_matcher)`` —
      one-shot snapshot helper that wraps
      :func:`kandra_runtime.snapshot_scan`.
    """
    imports = [
        "from collections.abc import Callable",
        "",
        "from kandra_runtime import Candidate, snapshot_scan",
    ]
    sections: list[str] = []

    if discovery.ble is not None:
        imports.append("from kandra_runtime import BleScanner")
        sections.append(_render_ble_scanner_section(discovery.ble))

    if discovery.http is not None:
        imports.insert(1, "from collections.abc import Iterable")
        imports.append("from kandra_runtime import HttpScanner")
        sections.append(_render_http_scanner_section(discovery.http))

    imports_block = "\n".join(imports)
    sections_block = "\n\n\n".join(sections)

    return f'''"""Generated discovery helpers. DO NOT EDIT — regenerate with `kandra build`."""

from __future__ import annotations

{imports_block}


{sections_block}
'''


def _render_ble_scanner_section(spec: BleDiscoverySpec) -> str:
    name_prefix_lit = _py_literal(spec.name_prefix)
    uuids_lit = (
        "frozenset((" + ", ".join(f'"{u.lower()}"' for u in spec.service_uuids) + ",))"
        if spec.service_uuids
        else "frozenset()"
    )
    mfr_lit = _py_literal(spec.manufacturer_id)

    return f'''# ---------------------------------------------------------------------------
# BLE discovery
# ---------------------------------------------------------------------------

_BLE_NAME_PREFIX: str | None = {name_prefix_lit}
_BLE_SERVICE_UUIDS: frozenset[str] = {uuids_lit}
_BLE_MANUFACTURER_ID: int | None = {mfr_lit}


def default_ble_matcher(candidate: Candidate) -> bool:
    """Return True iff `candidate` matches every manifest-declared BLE criterion."""
    if candidate.transport != "ble":
        return False
    if _BLE_NAME_PREFIX is not None:
        name = candidate.advertised_name or ""
        if not name.startswith(_BLE_NAME_PREFIX):
            return False
    if _BLE_SERVICE_UUIDS:
        adv_uuids = {{str(u).lower() for u in candidate.metadata.get("service_uuids", ())}}
        if not (_BLE_SERVICE_UUIDS & adv_uuids):
            return False
    if _BLE_MANUFACTURER_ID is not None:
        mdata = candidate.metadata.get("manufacturer_data") or {{}}
        if _BLE_MANUFACTURER_ID not in mdata:
            return False
    return True


def make_ble_scanner() -> BleScanner:
    """Construct a `BleScanner` instance (no criteria pre-applied)."""
    return BleScanner()


async def scan_ble(
    *,
    timeout: float = 10.0,
    matcher: Callable[[Candidate], bool] = default_ble_matcher,
) -> list[Candidate]:
    """One-shot BLE discovery: scan for `timeout` seconds and return matches."""
    return await snapshot_scan(make_ble_scanner(), matcher=matcher, timeout=timeout)'''


def _render_http_scanner_section(spec: HttpDiscoverySpec) -> str:
    urls_lit = (
        "(" + ", ".join(f'"{u}"' for u in spec.base_urls) + ",)"
    )
    header_lit = _py_literal(spec.server_header_prefix)

    return f'''# ---------------------------------------------------------------------------
# HTTP discovery
# ---------------------------------------------------------------------------

_HTTP_BASE_URLS: tuple[str, ...] = {urls_lit}
_HTTP_PROBE_PATH: str = "{spec.probe_path}"
_HTTP_SERVER_HEADER_PREFIX: str | None = {header_lit}


def default_http_matcher(candidate: Candidate) -> bool:
    """Return True iff `candidate` matches every manifest-declared HTTP criterion."""
    if candidate.transport != "http":
        return False
    if _HTTP_SERVER_HEADER_PREFIX is not None:
        advert = candidate.advertised_name or ""
        if not advert.startswith(_HTTP_SERVER_HEADER_PREFIX):
            return False
    return True


def make_http_scanner(base_urls: "Iterable[str] | None" = None) -> HttpScanner:
    """Construct an `HttpScanner`.

    By default uses manifest-declared `base_urls` and `probe_path`.
    Pass `base_urls` to override (useful for local dev against a sim
    on `http://localhost:PORT`)."""
    urls = list(base_urls) if base_urls is not None else list(_HTTP_BASE_URLS)
    return HttpScanner(urls, probe_path=_HTTP_PROBE_PATH)


async def scan_http(
    *,
    timeout: float = 10.0,
    matcher: Callable[[Candidate], bool] = default_http_matcher,
    base_urls: "Iterable[str] | None" = None,
) -> list[Candidate]:
    """One-shot HTTP discovery: probe each base_url, return matches within `timeout`.

    `base_urls` overrides the manifest-declared list (handy for local
    dev / sandbox scripts pointing at `http://localhost:PORT`)."""
    return await snapshot_scan(
        make_http_scanner(base_urls=base_urls), matcher=matcher, timeout=timeout
    )'''


def _py_literal(value: str | int | None) -> str:
    """Render a simple value as a Python literal for codegen."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return f'"{value}"'
    return repr(value)


def render_provenance(
    *,
    manifest_path: str,
    device_id: str,
    schema_version: int,
    kandra_version: str,
    generated_at: str,
    profile: str | None = None,
    manifest_sha256: str | None = None,
    profiles_sha256: str | None = None,
) -> str:
    """Emit a JSON provenance file recording what was built and from where.

    Args:
        manifest_path: Absolute path of the source manifest.
        device_id: The device id the SDK was generated for.
        schema_version: The manifest schema version.
        kandra_version: Version of the generator that produced the SDK.
        generated_at: UTC ISO-8601 timestamp of the build.
        profile: The audience profile used (``None`` for a non-pruned build).
        manifest_sha256: Hex digest of the manifest contents, when available.
        profiles_sha256: Hex digest of ``audience_profiles.yaml``, when a
            profile build was performed.

    Returns:
        The provenance JSON document, newline-terminated.
    """
    payload: dict[str, object] = {
        "device_id": device_id,
        "schema_version": schema_version,
        "manifest_path": manifest_path,
        "generator": "kandra",
        "kandra_version": kandra_version,
        "generated_at": generated_at,
    }
    if profile is not None:
        payload["profile"] = profile
    if manifest_sha256 is not None:
        payload["manifest_sha256"] = manifest_sha256
    if profiles_sha256 is not None:
        payload["audience_profiles_sha256"] = profiles_sha256
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _relative() -> str:
    """Return the relative import dot used by intra-package imports."""
    return ""


def _pascal(snake: str) -> str:
    """Convert ``snake_case`` to ``PascalCase``."""
    return "".join(part.capitalize() for part in snake.split("_"))


_RENDER_IDENT_SAFE = re.compile(r"[^0-9A-Za-z_]")


def _sanitize(name: str) -> str:
    """Render-side identifier sanitizer (mirrors ``build._sanitize``)."""
    return _RENDER_IDENT_SAFE.sub("_", name)
