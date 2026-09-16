"""Generator entrypoint: load a manifest, introspect handler classes, emit SDK files."""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from kandra.audience import (
    AUDIENCE_PROFILES_FILENAME,
    AudienceProfiles,
    audience_intersects,
    load_audience_profiles,
    posix_relpath,
    resolve_file_audience,
)
from kandra.closure import ClosureResult, ModuleFile, build_module_index, resolve_entry_points, walk_closure
from kandra.generator.render import (
    AttributeSpec,
    BleCommandWire,
    BleDiscoverySpec,
    CommandSpec,
    DiscoverySpec,
    EventSpec,
    HttpCommandWire,
    HttpDiscoverySpec,
    SubscribeWire,
    TransportSpec,
    render_client,
    render_init,
    render_provenance,
    render_registry,
    render_scanners,
    render_transports,
)
from kandra.leakage import assert_no_leakage
from kandra.loader import load_manifest
from kandra.manifest import Attribute, Command, Event, Manifest
from kandra.manifest.model import BleCommandSpec, HttpAttributeSpec, HttpCommandSpec
from kandra.vendor import internal_prefix, rewrite_imports, vendor_closure

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

_IDENT_SAFE = re.compile(r"[^A-Za-z0-9_]")


class BuildError(Exception):
    """Raised when SDK generation fails (handler import, type resolution, IO)."""


@dataclass(frozen=True)
class BuildResult:
    """What :func:`build_sdk` produced."""

    package_path: Path
    package_name: str
    files: tuple[Path, ...]


def build_sdk(
    manifest_path: Path,
    *,
    output_root: Path | None = None,
    clean: bool = False,
    verify: bool = True,
    typecheck: bool = False,
    profile: str | None = None,
    profiles_path: Path | None = None,
) -> BuildResult:
    """Generate the SDK package described by ``manifest_path``.

    ``output_root`` defaults to ``<manifest_dir>/dist``. The generated
    package lives at ``<output_root>/<device_id>_sdk/``. Source roots
    declared in the manifest are temporarily prepended to ``sys.path``
    while handler classes are introspected, then restored.

    When ``clean`` is true, the target package directory is removed
    before regeneration. This prevents stale files (e.g. a command
    removed from the manifest) from lingering in the output tree.

    When ``verify`` is true (the default), the freshly written package must
    byte-compile and import cleanly in an isolated subprocess before the
    build succeeds; ``typecheck`` additionally runs ``mypy --strict`` over
    it. Either check raises :class:`BuildError` on failure, so a manifest or
    handler flaw fails the build instead of shipping a broken SDK.

    When ``profile`` is set, the **audience-pruning + vendoring** pipeline runs
    instead of the plain import-from-source-roots build: the manifest is pruned
    to that profile's audiences, the handler import closure is vendored into
    ``<output_root>/<profile>/<device_id>_sdk/_internal`` with imports rewritten,
    a leakage scan enforces the profile's denylist, and the self-contained
    package is import-checked with the source roots *off* ``sys.path``.
    ``profiles_path`` overrides the default
    ``<manifest_dir>/audience_profiles.yaml`` location.
    """
    manifest_path = manifest_path.resolve()
    manifest = load_manifest(manifest_path)
    manifest_dir = manifest_path.parent
    resolved_roots = [(manifest_dir / r).resolve() for r in manifest.source_roots]
    package_name = f"{manifest.device.id}_sdk"

    if profile is not None:
        return _build_profile_sdk(
            manifest,
            manifest_path=manifest_path,
            manifest_dir=manifest_dir,
            resolved_roots=resolved_roots,
            package_name=package_name,
            output_root=output_root,
            profile=profile,
            profiles_path=profiles_path,
            clean=clean,
            verify=verify,
            typecheck=typecheck,
        )

    out_root = (output_root or (manifest_dir / "dist")).resolve()
    package_path = out_root / package_name

    with _augment_sys_path(resolved_roots):
        resolve_entry_points(_manifest_entry_points(manifest), resolved_roots)
        transport_specs = _resolve_transports(manifest)
        command_specs, _entry = _resolve_commands(manifest.commands)
        attribute_op_specs, attribute_specs, _attr_entry = _resolve_attributes(manifest.attributes)
        event_op_specs, event_specs, _event_entry = _resolve_events(manifest.events)

    discovery_spec = _resolve_discovery(manifest)
    device_class = _pascal(manifest.device.id) + "Client"
    provenance = _make_provenance(manifest_path=manifest_path, manifest=manifest)

    files = _write_package(
        package_path,
        device_class=device_class,
        device_id=manifest.device.id,
        provenance=provenance,
        transports=transport_specs,
        commands=command_specs,
        discovery=discovery_spec,
        attribute_op_specs=attribute_op_specs,
        attribute_specs=attribute_specs,
        event_op_specs=event_op_specs,
        event_specs=event_specs,
        clean=clean,
    )

    if verify:
        _verify_package(
            package_path,
            package_name,
            written_files=list(files),
            import_roots=[*resolved_roots, out_root],
            typecheck=typecheck,
        )

    return BuildResult(
        package_path=package_path,
        package_name=package_name,
        files=tuple(files),
    )


# ---------------------------------------------------------------------------
# Profile pipeline (audience prune -> closure -> vendor -> leakage scan)
# ---------------------------------------------------------------------------


def _build_profile_sdk(
    manifest: Manifest,
    *,
    manifest_path: Path,
    manifest_dir: Path,
    resolved_roots: list[Path],
    package_name: str,
    output_root: Path | None,
    profile: str,
    profiles_path: Path | None,
    clean: bool,
    verify: bool,
    typecheck: bool,
) -> BuildResult:
    """Audience-prune, vendor, and leakage-scan an SDK for a single profile."""
    profiles_file = profiles_path or (manifest_dir / AUDIENCE_PROFILES_FILENAME)
    profiles = load_audience_profiles(profiles_file)
    selected = profiles.profile(profile)  # AudienceError on unknown profile
    include = selected.include_audience

    surviving = [c for c in manifest.commands if audience_intersects(c.audience, include)]
    surviving_attrs = [a for a in manifest.attributes if audience_intersects(a.audience, include)]
    surviving_events = [e for e in manifest.events if audience_intersects(e.audience, include)]
    if not surviving and not surviving_attrs and not surviving_events:
        raise BuildError(
            f"profile {profile!r}: no commands, attributes, or events survive audience pruning "
            f"(include_audience={sorted(include)})"
        )

    out_root = (output_root or (manifest_dir / "dist")).resolve() / profile
    package_path = out_root / package_name

    with _augment_sys_path(resolved_roots):
        resolve_entry_points(_manifest_entry_points(manifest), resolved_roots)
        transport_specs = _resolve_transports(manifest)
        command_specs, entry_modules = _resolve_commands(surviving)
        attribute_op_specs, attribute_specs, attr_entry = _resolve_attributes(surviving_attrs)
        event_op_specs, event_specs, event_entry = _resolve_events(surviving_events)
    entry_modules |= attr_entry | event_entry

    used_transport_ids = {tid for cmd in surviving for tid in cmd.transports}
    used_transport_ids |= {tid for attr in surviving_attrs for tid in attr.transports}
    used_transport_ids |= {tid for event in surviving_events for tid in event.transports}
    entry_modules |= _transport_codec_entry_modules(manifest, used_transport_ids)

    closure = walk_closure(
        entry_modules,
        resolved_roots,
        extra_include=manifest.vendoring.extra_include,
        exclude=manifest.vendoring.exclude,
    )

    kept, dropped = _filter_closure_by_audience(closure, profiles, manifest_dir, include)
    _check_entry_files_survive(entry_modules, resolved_roots, dropped, manifest_dir, include)

    if clean and package_path.exists():
        shutil.rmtree(package_path)
    package_path.mkdir(parents=True, exist_ok=True)

    prefix = internal_prefix(package_name)
    tops = kept.top_level_packages
    vendored = vendor_closure(kept, resolved_roots, package_path, package_name)

    transport_specs = [_rewrite_transport_spec(t, tops, prefix) for t in transport_specs]
    command_specs = [_rewrite_command_spec(c, tops, prefix) for c in command_specs]
    attribute_op_specs = [_rewrite_command_spec(c, tops, prefix) for c in attribute_op_specs]
    attribute_specs = tuple(_rewrite_attribute_spec(a, tops, prefix) for a in attribute_specs)
    event_op_specs = [_rewrite_command_spec(c, tops, prefix) for c in event_op_specs]
    event_specs = tuple(_rewrite_event_spec(e, tops, prefix) for e in event_specs)

    discovery_spec = _resolve_discovery(manifest)
    device_class = _pascal(manifest.device.id) + "Client"
    provenance = _make_provenance(
        manifest_path=manifest_path,
        manifest=manifest,
        profile=profile,
        profiles_file=profiles_file,
    )

    glue = _write_package(
        package_path,
        device_class=device_class,
        device_id=manifest.device.id,
        provenance=provenance,
        transports=transport_specs,
        commands=command_specs,
        discovery=discovery_spec,
        attribute_op_specs=attribute_op_specs,
        attribute_specs=attribute_specs,
        event_op_specs=event_op_specs,
        event_specs=event_specs,
        clean=False,  # already cleaned + vendored above; don't wipe _internal
    )

    assert_no_leakage(package_path, selected.deny_substrings, base=package_path)

    if verify:
        # Self-containment check: import the vendored package with the source
        # roots OFF the path. Any reach back into the authoring tree fails here.
        _verify_package(
            package_path,
            package_name,
            written_files=[*glue, *vendored],
            import_roots=[out_root],
            typecheck=typecheck,
        )

    return BuildResult(
        package_path=package_path,
        package_name=package_name,
        files=tuple([*glue, *vendored]),
    )


def _transport_codec_entry_modules(manifest: Manifest, used_transport_ids: set[str]) -> frozenset[str]:
    """Return codec modules the generated registry imports for used non-HTTP transports.

    HTTP transports use the runtime's ``HttpJsonCodec`` (nothing to vendor); BLE
    and loopback/unknown transports import the user's codec, so its module must
    be part of the closure.
    """
    modules: set[str] = set()
    for t in manifest.transports:
        if t.id not in used_transport_ids:
            continue
        if t.family != "http" and t.codec is not None:
            modules.add(t.codec.split(":")[0])
        if t.adapter is not None:
            modules.add(t.adapter.split(":")[0])
    return frozenset(modules)


def _filter_closure_by_audience(
    closure: ClosureResult,
    profiles: AudienceProfiles,
    base_dir: Path,
    include: Sequence[str],
) -> tuple[ClosureResult, set[Path]]:
    """Drop closure files whose effective audience excludes the target profile.

    Modules are resolved with their ``# kandra-audience:`` header applied; assets
    use the YAML grant only. A build error is raised if any surviving file still
    imports a dropped one (the exact chain is reported).
    """
    include_set = set(include)
    dropped: set[Path] = set()
    kept_modules: list[ModuleFile] = []
    for module in closure.modules:
        rel = posix_relpath(module.path, base_dir)
        effective = resolve_file_audience(rel, module.path.read_text(encoding="utf-8"), profiles)
        if include_set & effective:
            kept_modules.append(module)
        else:
            dropped.add(module.path)

    for module in kept_modules:
        for dep in closure.imports.get(module.path, frozenset()):
            if dep in dropped:
                raise BuildError(
                    f"audience leak: {posix_relpath(module.path, base_dir)} is included for "
                    f"audience {sorted(include_set)} but imports "
                    f"{posix_relpath(dep, base_dir)}, which is excluded for that audience"
                )

    kept_assets: list[Path] = []
    for asset in closure.assets:
        grant = profiles.grant_for(posix_relpath(asset, base_dir))
        if include_set & grant:
            kept_assets.append(asset)
        else:
            dropped.add(asset)

    kept = ClosureResult(modules=tuple(kept_modules), assets=tuple(kept_assets), imports=closure.imports)
    return kept, dropped


def _check_entry_files_survive(
    entry_modules: frozenset[str],
    roots: list[Path],
    dropped: set[Path],
    base_dir: Path,
    include: Sequence[str],
) -> None:
    """Fail the build if a surviving command's own handler/model/codec was dropped."""
    index = build_module_index(roots)
    for dotted in sorted(entry_modules):
        module = index.get(dotted)
        if module is None:  # external module (e.g. kandra_runtime.*) — never vendored
            continue
        if module.path in dropped:
            raise BuildError(
                f"audience leak: entry module {dotted!r} "
                f"({posix_relpath(module.path, base_dir)}) is excluded for audience "
                f"{sorted(include)}, but a surviving command requires it"
            )


def _rewrite_transport_spec(spec: TransportSpec, tops: frozenset[str], prefix: str) -> TransportSpec:
    """Rewrite a transport spec's codec import to the vendored ``_internal`` namespace."""
    if spec.codec_import is None:
        return spec
    return replace(spec, codec_import=_rewrite_import_line(spec.codec_import, tops, prefix))


def _rewrite_command_spec(spec: CommandSpec, tops: frozenset[str], prefix: str) -> CommandSpec:
    """Rewrite a command spec's request/response and per-command codec imports to the vendored namespace."""
    return replace(
        spec,
        request_import=_rewrite_import_line(spec.request_import, tops, prefix),
        response_import=_rewrite_import_line(spec.response_import, tops, prefix),
        http_wires={tid: _rewrite_http_wire(w, tops, prefix) for tid, w in spec.http_wires.items()},
        ble_wires={tid: _rewrite_ble_wire(w, tops, prefix) for tid, w in spec.ble_wires.items()},
    )


def _rewrite_http_wire(wire: HttpCommandWire, tops: frozenset[str], prefix: str) -> HttpCommandWire:
    """Rewrite a per-command HTTP codec import to the vendored namespace (no-op without an override)."""
    if wire.codec_import is None:
        return wire
    return replace(wire, codec_import=_rewrite_import_line(wire.codec_import, tops, prefix))


def _rewrite_ble_wire(wire: BleCommandWire, tops: frozenset[str], prefix: str) -> BleCommandWire:
    """Rewrite a per-command BLE codec import to the vendored namespace (no-op without an override)."""
    if wire.codec_import is None:
        return wire
    return replace(wire, codec_import=_rewrite_import_line(wire.codec_import, tops, prefix))


def _rewrite_attribute_spec(spec: AttributeSpec, tops: frozenset[str], prefix: str) -> AttributeSpec:
    """Rewrite an attribute facade spec's value/write-ack imports to the vendored namespace."""
    return replace(
        spec,
        value_import=_rewrite_import_line(spec.value_import, tops, prefix),
        write_ack_import=_rewrite_import_line(spec.write_ack_import, tops, prefix),
    )


def _rewrite_event_spec(spec: EventSpec, tops: frozenset[str], prefix: str) -> EventSpec:
    """Rewrite an event facade spec's payload import to the vendored namespace."""
    return replace(spec, payload_import=_rewrite_import_line(spec.payload_import, tops, prefix))


def _rewrite_import_line(line: str, tops: frozenset[str], prefix: str) -> str:
    """Apply the vendoring import rewrite to a single generated ``from ... import`` line."""
    return rewrite_imports(line + "\n", tops, prefix).rstrip("\n")


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _make_provenance(
    *,
    manifest_path: Path,
    manifest: Manifest,
    profile: str | None = None,
    profiles_file: Path | None = None,
) -> str:
    """Build the ``_generated_from.json`` provenance document for this build."""
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    profiles_sha = (
        hashlib.sha256(profiles_file.read_bytes()).hexdigest()
        if profiles_file is not None and profiles_file.exists()
        else None
    )
    return render_provenance(
        manifest_path=str(manifest_path),
        device_id=manifest.device.id,
        schema_version=manifest.schema_version,
        kandra_version=_kandra_version(),
        generated_at=datetime.now(UTC).isoformat(),
        profile=profile,
        manifest_sha256=manifest_sha,
        profiles_sha256=profiles_sha,
    )


def _kandra_version() -> str:
    """Return the installed kandra version, or a sentinel when unavailable."""
    try:
        return metadata.version("kandra")
    except metadata.PackageNotFoundError:
        return "0+unknown"


# ---------------------------------------------------------------------------
# Manifest → spec translation
# ---------------------------------------------------------------------------


def _command_codec_entry_points(command: Command) -> list[tuple[str, str]]:
    """Return ``(module, label)`` pairs for a command's per-transport codec overrides."""
    eps: list[tuple[str, str]] = []
    wires: list[tuple[str, HttpCommandSpec | BleCommandSpec]] = [*command.http.items(), *command.ble.items()]
    for tid, spec in wires:
        if spec.codec is not None:
            eps.append((spec.codec.split(":")[0], f"command {command.id!r} codec on transport {tid!r}"))
    return eps


def _manifest_entry_points(manifest: Manifest) -> list[tuple[str, str]]:
    """Collect ``(module, label)`` for every dotted reference the generator resolves.

    Handlers, codecs, and adapters are all user code that gets vendored, so each
    must live under a source root (point ``source_roots`` at a shared tree if some
    are shared across devices).
    """
    eps: list[tuple[str, str]] = []
    for c in manifest.commands:
        if c.handler is not None:
            eps.append((c.handler.split(":")[0], f"command {c.id!r} handler"))
        eps.extend(_command_codec_entry_points(c))
    for a in manifest.attributes:
        if a.handler is not None:
            eps.append((a.handler.split(":")[0], f"attribute {a.id!r} handler"))
    for e in manifest.events:
        if e.handler is not None:
            eps.append((e.handler.split(":")[0], f"event {e.id!r} handler"))
    for t in manifest.transports:
        # HTTP ignores the manifest codec (built-in HttpJsonCodec), so it is not
        # an entry point the generator resolves -- mirror _transport_codec_entry_modules.
        if t.family != "http" and t.codec is not None:
            eps.append((t.codec.split(":")[0], f"transport {t.id!r} codec"))
        if t.adapter is not None:
            eps.append((t.adapter.split(":")[0], f"transport {t.id!r} adapter"))
    return eps


def _resolve_transports(manifest: Manifest) -> list[TransportSpec]:
    specs: list[TransportSpec] = []
    for t in manifest.transports:
        adapter_import: str | None = None
        adapter_alias: str | None = None
        if t.adapter is not None:
            a_module, a_class = t.adapter.split(":")
            _import_attr(a_module, a_class, what=f"transport {t.id!r} adapter")
            adapter_alias = f"_Adapter_{_sanitize(t.id)}"
            adapter_import = f"from {a_module} import {a_class} as {adapter_alias}"
        # HTTP uses the built-in HttpJsonCodec from kandra_runtime (parameterized
        # per-command from the http: blocks), so the manifest's transport.codec
        # is optional and ignored here. BLE imports the user's payload codec and
        # wraps it per-command with BleChannelCodec; loopback / unknown family
        # import the user's codec and wire it positionally as (request, response).
        if t.family == "http":
            specs.append(
                TransportSpec(
                    transport_id=t.id,
                    enum_member=_enum_member(t.id),
                    family=t.family,
                    codec_import=None,
                    codec_alias=None,
                    adapter_import=adapter_import,
                    adapter_alias=adapter_alias,
                )
            )
            continue
        # Non-HTTP families require a codec (enforced by Transport validation).
        assert t.codec is not None
        module_path, class_name = t.codec.split(":")
        _import_attr(module_path, class_name, what=f"transport {t.id!r} codec")
        alias = f"_Codec_{_sanitize(t.id)}"
        channels: tuple[tuple[str, str, str], ...] = ()
        if t.family == "ble":
            channels = tuple(
                (name, spec.write, spec.notify) for name, spec in t.channels.items()
            )
        specs.append(
            TransportSpec(
                transport_id=t.id,
                enum_member=_enum_member(t.id),
                family=t.family,
                codec_import=f"from {module_path} import {class_name} as {alias}",
                codec_alias=alias,
                channels=channels,
                adapter_import=adapter_import,
                adapter_alias=adapter_alias,
            )
        )
    return specs


def _wire_codec(
    codec: str | None, cmd_id: str, tid: str, entry_modules: set[str]
) -> tuple[str | None, str | None]:
    """Resolve an optional per-command codec override to an ``(import_line, alias)`` pair.

    Records the codec's module in ``entry_modules`` so it is vendored, and returns
    ``(None, None)`` when the command declares no override.
    """
    if codec is None:
        return None, None
    module_path, class_name = codec.split(":")
    _import_attr(module_path, class_name, what=f"command {cmd_id!r} codec on transport {tid!r}")
    entry_modules.add(module_path)
    alias = f"_CmdCodec_{_sanitize(cmd_id)}_{_sanitize(tid)}"
    return f"from {module_path} import {class_name} as {alias}", alias


def _resolve_commands(commands: Sequence[Command]) -> tuple[list[CommandSpec], frozenset[str]]:
    """Resolve manifest commands into render specs and their entry-point modules.

    Returns the per-command specs plus the set of dotted module names the
    generated glue will import (handler modules and the request/response model
    modules read off each handler). Those seed the vendoring import closure.
    """
    specs: list[CommandSpec] = []
    entry_modules: set[str] = set()
    for cmd in commands:
        assert cmd.handler is not None  # loader rejects null handlers
        module_path, class_name = cmd.handler.split(":")
        handler_cls = _import_attr(module_path, class_name, what=f"command {cmd.id!r} handler")

        request_cls = _read_handler_type(handler_cls, "request", cmd.id)
        response_cls = _read_handler_type(handler_cls, "response", cmd.id)
        entry_modules.update({module_path, request_cls.__module__, response_cls.__module__})

        safe = _sanitize(cmd.id)
        req_alias = f"_Req_{safe}"
        resp_alias = f"_Resp_{safe}"
        ns, method = _split_namespace(cmd.id)

        http_wires: dict[str, HttpCommandWire] = {}
        for tid, spec in cmd.http.items():
            codec_import, codec_alias = _wire_codec(spec.codec, cmd.id, tid, entry_modules)
            http_wires[tid] = HttpCommandWire(
                method=spec.method,
                path=spec.path,
                body_codec=spec.body_codec,
                response_codec=spec.response_codec,
                query_from_request=spec.query_from_request,
                expects_response=spec.expects_response,
                timeout=spec.timeout,
                codec_import=codec_import,
                codec_alias=codec_alias,
            )
        ble_wires: dict[str, BleCommandWire] = {}
        for tid, ble_spec in cmd.ble.items():
            codec_import, codec_alias = _wire_codec(ble_spec.codec, cmd.id, tid, entry_modules)
            ble_wires[tid] = BleCommandWire(
                channel=ble_spec.channel,
                expects_response=ble_spec.expects_response,
                timeout=ble_spec.timeout,
                codec_import=codec_import,
                codec_alias=codec_alias,
            )

        specs.append(
            CommandSpec(
                command_id=cmd.id,
                namespace=ns,
                method=method,
                timeout=cmd.timeout,
                request_import=(f"from {request_cls.__module__} import {request_cls.__name__} as {req_alias}"),
                request_alias=req_alias,
                response_import=(f"from {response_cls.__module__} import {response_cls.__name__} as {resp_alias}"),
                response_alias=resp_alias,
                transports=list(cmd.transports),
                http_wires=http_wires,
                ble_wires=ble_wires,
                capabilities=tuple(cmd.capabilities),
                idempotent=cmd.idempotent,
                retries=cmd.retries,
            )
        )
    return specs, frozenset(entry_modules)


def _resolve_attributes(
    attributes: Sequence[Attribute],
) -> tuple[list[CommandSpec], tuple[AttributeSpec, ...], frozenset[str]]:
    """Resolve manifest attributes into synthetic op commands, facade specs, and entry modules.

    Each attribute expands to one synthetic command per declared operation
    (``<id>.read`` / ``.write`` / ``.subscribe``) wired into the same registry
    the command layer uses, plus one :class:`AttributeSpec` describing the
    ``client.<namespace>.<name>`` facade. Read/subscribe requests carry the
    runtime :class:`NoArgs` sentinel; the handler's ``value`` type is the
    read/subscribe payload and the write input, and an optional ``write_ack``
    type (defaulting to ``value``) is the write response.
    """
    op_specs: list[CommandSpec] = []
    attr_specs: list[AttributeSpec] = []
    entry_modules: set[str] = set()

    for attr in attributes:
        if attr.handler is None:
            raise BuildError(
                f"attribute {attr.id!r}: a handler is required to generate its facade "
                "(set `handler: 'module:HandlerClass'`)"
            )
        if "." not in attr.id:
            raise BuildError(
                f"attribute id {attr.id!r} must be dotted ('<namespace>.<name>') so it maps to "
                "client.<namespace>.<name>"
            )
        module_path, class_name = attr.handler.split(":")
        handler_cls = _import_attr(module_path, class_name, what=f"attribute {attr.id!r} handler")

        value_cls = _read_attribute_type(handler_cls, "value", attr.id, required=True)
        assert value_cls is not None  # required=True raises rather than returning None
        write_ack_cls = _read_attribute_type(handler_cls, "write_ack", attr.id, required=False) or value_cls
        entry_modules.update({module_path, value_cls.__module__, write_ack_cls.__module__})

        safe = _sanitize(attr.id)
        namespace, attr_name = _split_namespace(attr.id)
        value_alias = f"_Val_{safe}"
        value_import = f"from {value_cls.__module__} import {value_cls.__name__} as {value_alias}"
        if write_ack_cls is value_cls:
            ack_alias, ack_import = value_alias, value_import
        else:
            ack_alias = f"_Ack_{safe}"
            ack_import = f"from {write_ack_cls.__module__} import {write_ack_cls.__name__} as {ack_alias}"

        operations = tuple(attr.operations)
        for op in operations:
            op_specs.append(
                _attribute_op_spec(
                    attr,
                    op,
                    value_alias=value_alias,
                    value_import=value_import,
                    ack_alias=ack_alias,
                    ack_import=ack_import,
                )
            )

        attr_specs.append(
            AttributeSpec(
                attr_id=attr.id,
                namespace=namespace,
                attr_name=attr_name,
                class_name=f"_{_pascal(namespace)}{_pascal(attr_name)}Attribute",
                sync_class_name=f"_Sync{_pascal(namespace)}{_pascal(attr_name)}Attribute",
                value_import=value_import,
                value_alias=value_alias,
                write_ack_import=ack_import,
                write_ack_alias=ack_alias,
                operations=operations,
                subscribe_wires=(_attribute_subscribe_wires(attr) if "subscribe" in operations else ()),
            )
        )

    return op_specs, tuple(attr_specs), frozenset(entry_modules)


def _attribute_op_spec(
    attr: Attribute,
    op: str,
    *,
    value_alias: str,
    value_import: str,
    ack_alias: str,
    ack_import: str,
) -> CommandSpec:
    """Build the synthetic :class:`CommandSpec` for one attribute operation."""
    command_id = f"{attr.id}.{op}"
    namespace, method = _split_namespace(command_id)
    if op == "write":
        req_alias, req_import = value_alias, value_import
        resp_alias, resp_import = ack_alias, ack_import
    else:  # read / subscribe carry the empty NoArgs request; response is the value.
        req_alias, req_import = "_NoArgs", "from kandra_runtime import NoArgs as _NoArgs"
        resp_alias, resp_import = value_alias, value_import

    http_wires = {tid: _attribute_http_wire(op, spec) for tid, spec in attr.http.items()}
    ble_wires = {
        tid: BleCommandWire(channel=spec.channel, expects_response=True, timeout=spec.timeout)
        for tid, spec in attr.ble.items()
    }
    return CommandSpec(
        command_id=command_id,
        namespace=namespace,
        method=method,
        timeout=None,
        request_import=req_import,
        request_alias=req_alias,
        response_import=resp_import,
        response_alias=resp_alias,
        transports=list(attr.transports),
        http_wires=http_wires,
        ble_wires=ble_wires,
        capabilities=tuple(attr.capabilities),
    )


def _attribute_http_wire(op: str, spec: HttpAttributeSpec) -> HttpCommandWire:
    """Translate one attribute HTTP op block into an :class:`HttpCommandWire`."""
    if op == "subscribe":
        sub = spec.subscribe
        assert sub is not None  # validation guarantees a block for every declared op
        return HttpCommandWire(
            method="GET",
            path=sub.path,
            body_codec="none",
            response_codec="json",
            query_from_request=False,
            expects_response=True,
            timeout=sub.timeout,
        )
    http_op = spec.read if op == "read" else spec.write
    assert http_op is not None  # validation guarantees a block for every declared op
    return HttpCommandWire(
        method=http_op.method,
        path=http_op.path,
        body_codec=http_op.body_codec,
        response_codec=http_op.response_codec,
        query_from_request=http_op.query_from_request,
        expects_response=True,
        timeout=http_op.timeout,
    )


def _attribute_subscribe_wires(attr: Attribute) -> tuple[SubscribeWire, ...]:
    """Build the per-transport subscribe delivery table for one attribute."""
    wires: list[SubscribeWire] = []
    for tid in attr.transports:
        if tid in attr.http:
            sub = attr.http[tid].subscribe
            assert sub is not None  # validation guarantees a subscribe block here
            wires.append(
                SubscribeWire(enum_member=_enum_member(tid), mode=sub.mode, interval=sub.interval)
            )
        elif tid in attr.ble:
            wires.append(SubscribeWire(enum_member=_enum_member(tid), mode="ble", interval=None))
    return tuple(wires)


def _read_attribute_type(handler_cls: type, attr: str, attribute_id: str, *, required: bool) -> type | None:
    """Read a class-valued attribute (``value`` / ``write_ack``) off an attribute handler."""
    value = getattr(handler_cls, attr, None)
    if value is None:
        if required:
            raise BuildError(
                f"attribute {attribute_id!r}: handler {handler_cls.__module__}:{handler_cls.__name__} "
                f"is missing required attribute {attr!r} (set `{attr} = SomeDataclass`)"
            )
        return None
    if not isinstance(value, type):
        raise BuildError(
            f"attribute {attribute_id!r}: handler.{attr} must be a class, got {type(value).__name__}"
        )
    return value


def _resolve_events(
    events: Sequence[Event],
) -> tuple[list[CommandSpec], tuple[EventSpec, ...], frozenset[str]]:
    """Resolve manifest events into synthetic subscribe commands, facade specs, and entry modules.

    Each event becomes one synthetic ``<id>.subscribe`` command wired into the
    same registry the command layer uses, plus one :class:`EventSpec` describing
    the ``client.<namespace>.<name>.subscribe()`` facade. The subscribe request
    carries the runtime :class:`NoArgs` sentinel; the handler's ``payload`` type
    is the streamed emission payload.
    """
    op_specs: list[CommandSpec] = []
    event_specs: list[EventSpec] = []
    entry_modules: set[str] = set()

    for event in events:
        if event.handler is None:
            raise BuildError(
                f"event {event.id!r}: a handler is required to generate its facade "
                "(set `handler: 'module:HandlerClass'`)"
            )
        if "." not in event.id:
            raise BuildError(
                f"event id {event.id!r} must be dotted ('<namespace>.<name>') so it maps to "
                "client.<namespace>.<name>"
            )
        module_path, class_name = event.handler.split(":")
        handler_cls = _import_attr(module_path, class_name, what=f"event {event.id!r} handler")
        payload_cls = _read_event_payload(handler_cls, event.id)
        entry_modules.update({module_path, payload_cls.__module__})

        safe = _sanitize(event.id)
        namespace, event_name = _split_namespace(event.id)
        payload_alias = f"_Evt_{safe}"
        payload_import = f"from {payload_cls.__module__} import {payload_cls.__name__} as {payload_alias}"

        op_specs.append(_event_op_spec(event, payload_alias, payload_import))
        event_specs.append(
            EventSpec(
                event_id=event.id,
                namespace=namespace,
                event_name=event_name,
                class_name=f"_{_pascal(namespace)}{_pascal(event_name)}Event",
                payload_import=payload_import,
                payload_alias=payload_alias,
                subscribe_wires=_event_subscribe_wires(event),
            )
        )

    return op_specs, tuple(event_specs), frozenset(entry_modules)


def _event_op_spec(event: Event, payload_alias: str, payload_import: str) -> CommandSpec:
    """Build the synthetic ``<id>.subscribe`` :class:`CommandSpec` for one event."""
    command_id = f"{event.id}.subscribe"
    namespace, method = _split_namespace(command_id)
    http_wires = {
        tid: HttpCommandWire(
            method="GET",
            path=spec.path,
            body_codec="none",
            response_codec="json",
            query_from_request=False,
            expects_response=True,
            timeout=spec.timeout,
        )
        for tid, spec in event.http.items()
    }
    ble_wires = {
        tid: BleCommandWire(channel=spec.channel, expects_response=True, timeout=spec.timeout)
        for tid, spec in event.ble.items()
    }
    return CommandSpec(
        command_id=command_id,
        namespace=namespace,
        method=method,
        timeout=None,
        request_import="from kandra_runtime import NoArgs as _NoArgs",
        request_alias="_NoArgs",
        response_import=payload_import,
        response_alias=payload_alias,
        transports=list(event.transports),
        http_wires=http_wires,
        ble_wires=ble_wires,
        capabilities=tuple(event.capabilities),
    )


def _event_subscribe_wires(event: Event) -> tuple[SubscribeWire, ...]:
    """Build the per-transport subscribe delivery table for one event."""
    wires: list[SubscribeWire] = []
    for tid in event.transports:
        if tid in event.http:
            spec = event.http[tid]
            wires.append(SubscribeWire(enum_member=_enum_member(tid), mode=spec.mode, interval=spec.interval))
        elif tid in event.ble:
            wires.append(SubscribeWire(enum_member=_enum_member(tid), mode="ble", interval=None))
    return tuple(wires)


def _read_event_payload(handler_cls: type, event_id: str) -> type:
    """Read the required class-valued ``payload`` type off an event handler."""
    value = getattr(handler_cls, "payload", None)
    if value is None:
        raise BuildError(
            f"event {event_id!r}: handler {handler_cls.__module__}:{handler_cls.__name__} "
            "is missing required attribute 'payload' (set `payload = SomeDataclass`)"
        )
    if not isinstance(value, type):
        raise BuildError(f"event {event_id!r}: handler.payload must be a class, got {type(value).__name__}")
    return value


def _resolve_discovery(manifest: Manifest) -> DiscoverySpec | None:
    """Translate the manifest's discovery block into a render-side spec."""
    if manifest.discovery is None:
        return None
    ble = None
    if manifest.discovery.ble is not None:
        b = manifest.discovery.ble
        ble = BleDiscoverySpec(
            name_prefix=b.name_prefix,
            service_uuids=tuple(b.service_uuids),
            manufacturer_id=b.manufacturer_id,
        )
    http = None
    if manifest.discovery.http is not None:
        h = manifest.discovery.http
        http = HttpDiscoverySpec(
            base_urls=tuple(h.base_urls),
            probe_path=h.probe_path,
            server_header_prefix=h.server_header_prefix,
        )
    return DiscoverySpec(ble=ble, http=http)


def _read_handler_type(handler_cls: type, attr: str, command_id: str) -> type:
    value = getattr(handler_cls, attr, None)
    if value is None:
        raise BuildError(
            f"command {command_id!r}: handler {handler_cls.__module__}:{handler_cls.__name__} "
            f"is missing required attribute {attr!r} (set `{attr} = SomeDataclass`)"
        )
    if not isinstance(value, type):
        raise BuildError(
            f"command {command_id!r}: handler.{attr} must be a class, "
            f"got {type(value).__name__}"
        )
    return value


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


def _write_package(
    package_path: Path,
    *,
    device_class: str,
    device_id: str,
    provenance: str,
    transports: list[TransportSpec],
    commands: list[CommandSpec],
    discovery: DiscoverySpec | None,
    attribute_op_specs: list[CommandSpec] | None = None,
    attribute_specs: tuple[AttributeSpec, ...] = (),
    event_op_specs: list[CommandSpec] | None = None,
    event_specs: tuple[EventSpec, ...] = (),
    clean: bool = False,
) -> list[Path]:
    if clean and package_path.exists():
        shutil.rmtree(package_path)
    package_path.mkdir(parents=True, exist_ok=True)

    # The registry carries real commands plus the synthetic attribute + event ops;
    # the client facade groups real commands into namespace methods, attributes
    # into read/write/subscribe sub-objects, and events into subscribe sub-objects.
    registry_commands = commands + list(attribute_op_specs or []) + list(event_op_specs or [])
    # op id -> required capability tags, for every gated op (commands + attr/event ops).
    capability_map = {c.command_id: c.capabilities for c in registry_commands if c.capabilities}
    files: list[tuple[str, str]] = [
        ("__init__.py", render_init(device_class, discovery=discovery)),
        ("py.typed", ""),
        ("transports.py", render_transports(transports)),
        ("registry.py", render_registry(registry_commands, transports)),
        (
            "client.py",
            render_client(
                device_class,
                commands,
                device_id=device_id,
                transports=transports,
                discovery=discovery,
                attributes=attribute_specs,
                events=event_specs,
                capabilities=capability_map,
            ),
        ),
        ("_generated_from.json", provenance),
    ]
    if discovery is not None:
        files.append(("scanners.py", render_scanners(discovery)))
    written: list[Path] = []
    for name, content in files:
        target = package_path / name
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written


# ---------------------------------------------------------------------------
# Post-generation verification
# ---------------------------------------------------------------------------


def _verify_package(
    package_path: Path,
    package_name: str,
    *,
    written_files: list[Path],
    import_roots: list[Path],
    typecheck: bool = False,
) -> None:
    """Fail the build unless the freshly generated package is sound.

    Escalating checks so a manifest or handler flaw surfaces here rather than
    in a shipped SDK:

    1. Byte-compile every generated module (precise syntax errors).
    2. Import the package in a hermetic subprocess whose search path is
       exactly ``import_roots`` plus the interpreter's own site-packages --
       catching bad imports, missing symbols, and import-time exceptions.
    3. Optionally run ``mypy --strict`` over the package.

    Only files this build wrote are compiled; unrelated files a user may have
    dropped into an additive (non-``clean``) output tree are left alone.
    """
    _compile_generated_sources(written_files)
    _import_in_clean_subprocess(package_name, import_roots)
    if typecheck:
        _typecheck_generated_package(package_path, import_roots)


def _compile_generated_sources(written_files: list[Path]) -> None:
    for py_file in written_files:
        if py_file.suffix != ".py":
            continue
        try:
            compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")
        except SyntaxError as exc:
            raise BuildError(
                f"generated module {py_file.name} failed to compile (line {exc.lineno}): {exc.msg}"
            ) from exc


def _clean_pythonpath(import_roots: list[Path]) -> str:
    return os.pathsep.join(str(r) for r in import_roots)


def _import_in_clean_subprocess(package_name: str, import_roots: list[Path]) -> None:
    env = {**os.environ, "PYTHONPATH": _clean_pythonpath(import_roots)}
    proc = subprocess.run(
        [sys.executable, "-c", f"import {package_name}"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise BuildError(f"generated package {package_name!r} does not import cleanly:\n{detail}")


def _typecheck_generated_package(package_path: Path, import_roots: list[Path]) -> None:
    probe = subprocess.run([sys.executable, "-c", "import mypy"], capture_output=True, check=False)
    if probe.returncode != 0:
        raise BuildError("cannot type-check generated package: mypy is not installed (install it or drop --typecheck)")
    env = {**os.environ, "MYPYPATH": _clean_pythonpath(import_roots)}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            # Types from an unfollowed dep (e.g. an editable/untyped runtime) degrade to
            # Any; that's about the environment, not the generated code, so don't fail on it.
            "--disable-error-code=no-any-unimported",
            "--no-error-summary",
            str(package_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr).strip()
        raise BuildError(f"generated package failed --strict type checking:\n{detail}")


# ---------------------------------------------------------------------------
# sys.path / imports
# ---------------------------------------------------------------------------


@contextmanager
def _augment_sys_path(roots: list[Path]) -> Iterator[None]:
    """Prepend ``roots`` to ``sys.path`` and clear cached imports under those roots on exit."""
    added = [str(r) for r in roots if r.exists()]
    missing = [r for r in roots if not r.exists()]
    if missing:
        raise BuildError(
            "manifest source_roots do not exist: " + ", ".join(str(m) for m in missing)
        )
    sys.path[:0] = added
    snapshot = set(sys.modules)
    try:
        yield
    finally:
        for p in added:
            with suppress(ValueError):
                sys.path.remove(p)
        # Drop modules imported during introspection so repeated builds in
        # the same process see fresh module objects.
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]


def _import_attr(module_path: str, attr: str, *, what: str) -> type:
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise BuildError(f"{what}: cannot import module {module_path!r}: {exc}") from exc
    try:
        value = getattr(module, attr)
    except AttributeError as exc:
        raise BuildError(
            f"{what}: module {module_path!r} has no attribute {attr!r}"
        ) from exc
    if not isinstance(value, type):
        raise BuildError(
            f"{what}: {module_path}:{attr} resolved to {type(value).__name__}, expected a class"
        )
    return value


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def _sanitize(dotted: str) -> str:
    return _IDENT_SAFE.sub("_", dotted)


def _enum_member(transport_id: str) -> str:
    return _sanitize(transport_id).upper()


def _split_namespace(command_id: str) -> tuple[str, str]:
    parts = command_id.split(".")
    if len(parts) < 2:
        raise BuildError(
            f"command id {command_id!r} must contain at least one dot "
            "(format: '<namespace>.<method>')"
        )
    return parts[0], "_".join(parts[1:])


def _pascal(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.split("_"))


# ---------------------------------------------------------------------------
# Module re-export
# ---------------------------------------------------------------------------

__all__ = ["BuildError", "BuildResult", "build_sdk"]
