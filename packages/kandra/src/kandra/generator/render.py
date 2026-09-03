"""String templates for the files emitted by `kandra build`.

These functions are intentionally pure: they take simple data (paths,
names, command specs) and return rendered Python source.  Anything that
involves filesystem or import-system side effects lives in
:mod:`kandra.generator.build`.
"""

from __future__ import annotations

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

{runtime_imports}

{imports_block}

from {_relative()}.transports import TransportId

COMMANDS: dict[str, dict[TransportId, Command]] = {{
{entries_block}
}}
'''


def _render_command_entry(c: CommandSpec, t: TransportSpec) -> str:
    """Render one ``TransportId.X: Command(...)`` line for the registry."""
    indent = "        "
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
            f"{indent}    {timeout_arg}expects_response={expects},\n"
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
            f"{indent}    {timeout_arg}expects_response={expects},\n"
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
        f"{indent}    interpreter=always_accepted_interpreter{timeout_arg},\n"
        f"{indent}),"
    )


def render_client(
    device_class: str,
    commands: list[CommandSpec],
    *,
    device_id: str,
    transports: list[TransportSpec],
    discovery: DiscoverySpec | None = None,
) -> str:
    """Emit the user-facing async client facade plus a sync wrapper."""
    # Group commands by namespace, preserving manifest order.
    namespaces: dict[str, list[CommandSpec]] = {}
    for c in commands:
        namespaces.setdefault(c.namespace, []).append(c)

    namespace_classes: list[str] = []
    sync_namespace_classes: list[str] = []
    namespace_assigns: list[str] = []
    sync_namespace_assigns: list[str] = []
    sync_class = f"Sync{device_class}"

    for ns, cmds in namespaces.items():
        async_class_name = f"_{_pascal(ns)}Namespace"
        sync_class_name = f"_Sync{_pascal(ns)}Namespace"
        async_methods: list[str] = []
        sync_methods: list[str] = []
        for c in cmds:
            async_methods.append(
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
            sync_methods.append(
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
        async_methods_block = "\n\n".join(async_methods)
        sync_methods_block = "\n\n".join(sync_methods)
        namespace_classes.append(
            f"class {async_class_name}:\n"
            f'    """`{ns}.*` async operations."""\n\n'
            f"    def __init__(self, client: {device_class}) -> None:\n"
            f"        self._client = client\n\n"
            f"{async_methods_block}"
        )
        sync_namespace_classes.append(
            f"class {sync_class_name}:\n"
            f'    """`{ns}.*` sync operations."""\n\n'
            f"    def __init__(self, async_client: {device_class}) -> None:\n"
            f"        self._async = async_client\n\n"
            f"{sync_methods_block}"
        )
        namespace_assigns.append(f"        self.{ns} = {async_class_name}(self)")
        sync_namespace_assigns.append(f"        self.{ns} = {sync_class_name}(self._async)")

    type_alias_imports = "\n".join(
        sorted({c.request_import for c in commands} | {c.response_import for c in commands})
    )
    namespace_classes_block = "\n\n\n".join(namespace_classes)
    sync_namespace_classes_block = "\n\n\n".join(sync_namespace_classes)
    namespace_assigns_block = "\n".join(namespace_assigns)
    sync_namespace_assigns_block = "\n".join(sync_namespace_assigns)

    connect_section = _render_connect_section(device_id, transports, discovery=discovery)

    return f'''"""Generated client facade. DO NOT EDIT — regenerate with `kandra build`."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from types import TracebackType
from typing import Any, cast

from kandra_runtime import Command, Result, Transport, dispatch, format_failure
{connect_section.imports}

{type_alias_imports}

from {_relative()}.registry import COMMANDS
from {_relative()}.transports import TransportId

{connect_section.module_level}

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
{namespace_assigns_block}

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

    async def _dispatch(
        self,
        command_id: str,
        request: Any,
        *,
        via: TransportId | None,
    ) -> Result[Any] | None:
        """Internal: resolve the transport, run the command, fire the hook if armed."""
        per_transport = COMMANDS[command_id]
        if via is not None:
            if via not in per_transport:
                raise ValueError(
                    f"command {{command_id!r}} does not support transport {{via.value!r}}"
                )
            if via not in self._transports:
                raise ValueError(f"transport {{via.value!r}} is not wired into this client")
            chosen = via
        else:
            chosen_opt = next((t for t in per_transport if t in self._transports), None)
            if chosen_opt is None:
                raise ValueError(f"no wired transport supports command {{command_id!r}}")
            chosen = chosen_opt
        command = cast(Command[Any, Any, Any, Any], per_transport[chosen])
        result = await dispatch(command, self._transports[chosen], request)
        if (
            result is not None
            and not result.accepted
            and self.on_non_accepted is not None
            and not self._suppress_hook
        ):
            self.on_non_accepted(format_failure(result))
        return result


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


{namespace_classes_block}


{sync_namespace_classes_block}
'''


@dataclass(frozen=True)
class _ConnectSection:
    """Three rendered blocks spliced into ``client.py`` for the connect feature."""

    imports: str  # extra top-level imports
    module_level: str  # module-level constants + helpers (channel maps, factories)
    client_methods: str  # methods added to the device class


def _render_discover_and_connect(families: list[str]) -> str:
    """Render the ``discover_and_connect()`` classmethod body.

    ``families`` lists the manifest families that have *both* a
    transport entry and a discovery block — those are the families
    eligible for one-shot scan + enroll.
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
            f'                    f"discover_and_connect: no {fam.upper()} candidates found "\n'
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

        # Normalize enrollment argument.
        known_families: tuple[str, ...] = ({known_families_repr},)
        if isinstance(enrollment, Mapping):
            enrollment_map: dict[str, Enrollment] = {{
                fam: adapter for fam, adapter in enrollment.items()
                if fam in known_families
            }}
        else:
            if len(known_families) != 1:
                raise ValueError(
                    "discover_and_connect: this device has multiple discoverable "
                    f"families ({{known_families}}); pass enrollment as a mapping "
                    "keyed by family, not a single Enrollment instance"
                )
            enrollment_map = {{known_families[0]: enrollment}}

        if not enrollment_map:
            raise ValueError(
                "discover_and_connect: no enrollment adapter matches this device's "
                f"discoverable families {{known_families}}"
            )

        # Scan + enroll each requested family.
        collected: dict[str, Identity] = {{}}
{scan_blocks_joined}

        # Build a single Identity (or wrap in CompositeIdentity for multi-family).
        if len(collected) == 1:
            (only_identity,) = collected.values()
            identity_to_save: Identity = only_identity
        else:
            identity_to_save = CompositeIdentity(
                saved_name=saved_name,
                components=dict(collected),
            )
        store.save(identity_to_save)

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

    runtime_extra_imports: list[str] = ["BleIdentity", "CompositeIdentity", "HttpIdentity"]
    if has_ble:
        runtime_extra_imports.append("BleTransport")
    if has_http:
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
    imports = imports_block

    # Per-BLE-transport channel maps + factory entries.
    module_lines: list[str] = [f'_DEFAULT_APP_NAME = "{device_id}_sdk"']
    factory_entries: list[str] = []
    for t in transports:
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
                f"lambda ident: BleTransport.from_identity(ident, channels={chan_var})),"
            )
        elif t.family == "http":
            factory_entries.append(
                f'    (TransportId.{t.enum_member}, "http", '
                "lambda ident: HttpTransport.from_identity(ident)),"
            )
        # Loopback / unknown families: no from_identity available; skip.

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

    module_level = "\n\n".join([*module_lines, factories_block, identity_helper])

    client_methods = '''    @classmethod
    async def connect(
        cls,
        saved_name: str,
        *,
        store: "IdentityStore | None" = None,
        transports: "Collection[TransportId] | None" = None,
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

        Raises
        ------
        IdentityNotFoundError:
            ``saved_name`` is not present in the store.
        ValueError:
            The saved identity supplied no usable transport (e.g. it
            stores only HTTP credentials but ``transports={TransportId.BLE}``
            was requested).
        TransportError:
            A transport's ``open()`` call failed; all transports opened
            so far in this call are closed before re-raising.
        """
        if store is None:
            store = PlatformDirsJsonStore(app_name=_DEFAULT_APP_NAME)
        identity = store.load(saved_name)

        filter_ids: "set[TransportId] | None" = (
            None if transports is None else set(transports)
        )

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

        if not built:
            raise ValueError(
                f"saved identity {saved_name!r} supplied no transports "
                f"matching this device's manifest"
            )

        client = cls(transports=built)
        client._owned_transports = built
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


def render_provenance(manifest_path: str, device_id: str, schema_version: int) -> str:
    """Emit a JSON provenance file recording what was built and from where."""
    import json as _json

    return _json.dumps(
        {
            "device_id": device_id,
            "schema_version": schema_version,
            "manifest_path": manifest_path,
            "generator": "kandra",
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


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
