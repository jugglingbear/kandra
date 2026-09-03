"""Generator entrypoint: load a manifest, introspect handler classes, emit SDK files."""

from __future__ import annotations

import importlib
import re
import shutil
import sys
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kandra.generator.render import (
    BleCommandWire,
    BleDiscoverySpec,
    CommandSpec,
    DiscoverySpec,
    HttpCommandWire,
    HttpDiscoverySpec,
    TransportSpec,
    render_client,
    render_init,
    render_provenance,
    render_registry,
    render_scanners,
    render_transports,
)
from kandra.loader import load_manifest
from kandra.manifest import Manifest

if TYPE_CHECKING:
    from collections.abc import Iterator

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
) -> BuildResult:
    """Generate the SDK package described by ``manifest_path``.

    ``output_root`` defaults to ``<manifest_dir>/dist``. The generated
    package lives at ``<output_root>/<device_id>_sdk/``. Source roots
    declared in the manifest are temporarily prepended to ``sys.path``
    while handler classes are introspected, then restored.

    When ``clean`` is true, the target package directory is removed
    before regeneration. This prevents stale files (e.g. a command
    removed from the manifest) from lingering in the output tree.
    """
    manifest_path = manifest_path.resolve()
    manifest = load_manifest(manifest_path)

    manifest_dir = manifest_path.parent
    resolved_roots = [(manifest_dir / r).resolve() for r in manifest.source_roots]

    package_name = f"{manifest.device.id}_sdk"
    out_root = (output_root or (manifest_dir / "dist")).resolve()
    package_path = out_root / package_name

    with _augment_sys_path(resolved_roots):
        transport_specs = _resolve_transports(manifest)
        command_specs = _resolve_commands(manifest)

    discovery_spec = _resolve_discovery(manifest)

    device_class = _pascal(manifest.device.id) + "Client"

    files = _write_package(
        package_path,
        device_class=device_class,
        manifest_path=str(manifest_path),
        device_id=manifest.device.id,
        schema_version=manifest.schema_version,
        transports=transport_specs,
        commands=command_specs,
        discovery=discovery_spec,
        clean=clean,
    )

    return BuildResult(
        package_path=package_path,
        package_name=package_name,
        files=tuple(files),
    )


# ---------------------------------------------------------------------------
# Manifest → spec translation
# ---------------------------------------------------------------------------


def _resolve_transports(manifest: Manifest) -> list[TransportSpec]:
    specs: list[TransportSpec] = []
    for t in manifest.transports:
        module_path, class_name = t.codec.split(":")
        # For HTTP we use built-in HttpJsonCodec from kandra_runtime
        # (parameterized per-command from the http: blocks); the
        # manifest's transport.codec is parsed for round-trip
        # consistency but not imported. For BLE we import the user's
        # payload codec and wrap it per-command with BleChannelCodec.
        # For loopback / unknown family we import the user's codec
        # and wire it positionally with (request_type, response_type).
        if t.family == "http":
            specs.append(
                TransportSpec(
                    transport_id=t.id,
                    enum_member=_enum_member(t.id),
                    family=t.family,
                    codec_import=None,
                    codec_alias=None,
                )
            )
            continue
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
            )
        )
    return specs


def _resolve_commands(manifest: Manifest) -> list[CommandSpec]:
    specs: list[CommandSpec] = []
    for cmd in manifest.commands:
        assert cmd.handler is not None  # loader rejects null handlers
        module_path, class_name = cmd.handler.split(":")
        handler_cls = _import_attr(module_path, class_name, what=f"command {cmd.id!r} handler")

        request_cls = _read_handler_type(handler_cls, "request", cmd.id)
        response_cls = _read_handler_type(handler_cls, "response", cmd.id)

        safe = _sanitize(cmd.id)
        req_alias = f"_Req_{safe}"
        resp_alias = f"_Resp_{safe}"
        ns, method = _split_namespace(cmd.id)

        http_wires = {
            tid: HttpCommandWire(
                method=spec.method,
                path=spec.path,
                body_codec=spec.body_codec,
                response_codec=spec.response_codec,
                query_from_request=spec.query_from_request,
                expects_response=spec.expects_response,
                timeout=spec.timeout,
            )
            for tid, spec in cmd.http.items()
        }
        ble_wires = {
            tid: BleCommandWire(
                channel=spec.channel,
                expects_response=spec.expects_response,
                timeout=spec.timeout,
            )
            for tid, spec in cmd.ble.items()
        }

        specs.append(
            CommandSpec(
                command_id=cmd.id,
                namespace=ns,
                method=method,
                timeout=cmd.timeout,
                request_import=(
                    f"from {request_cls.__module__} import {request_cls.__name__} as {req_alias}"
                ),
                request_alias=req_alias,
                response_import=(
                    f"from {response_cls.__module__} import {response_cls.__name__} as {resp_alias}"
                ),
                response_alias=resp_alias,
                transports=list(cmd.transports),
                http_wires=http_wires,
                ble_wires=ble_wires,
            )
        )
    return specs


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
    manifest_path: str,
    device_id: str,
    schema_version: int,
    transports: list[TransportSpec],
    commands: list[CommandSpec],
    discovery: DiscoverySpec | None,
    clean: bool = False,
) -> list[Path]:
    if clean and package_path.exists():
        shutil.rmtree(package_path)
    package_path.mkdir(parents=True, exist_ok=True)

    files: list[tuple[str, str]] = [
        ("__init__.py", render_init(device_class, discovery=discovery)),
        ("py.typed", ""),
        ("transports.py", render_transports(transports)),
        ("registry.py", render_registry(commands, transports)),
        (
            "client.py",
            render_client(
                device_class,
                commands,
                device_id=device_id,
                transports=transports,
                discovery=discovery,
            ),
        ),
        (
            "_generated_from.json",
            render_provenance(manifest_path, device_id, schema_version),
        ),
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
