"""Pydantic models for the manifest YAML.

These models define the *wiring* of a generated SDK: which device, which
transports, which commands, which audiences. They deliberately do **not**
describe Python types or behavior — that's all in user-authored Python,
referenced via dotted paths and resolved at generation time.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# ---------------------------------------------------------------------------
# Primitive types
# ---------------------------------------------------------------------------

# Identifiers used as YAML keys / facade attr names: dotted, snake_case segments.
# Examples: "media", "media.list_files", "settings.resolution".
IdentifierStr = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$", strip_whitespace=True),
]

# Audience tags. Lowercase with `_` or `-` so users can pick their own
# taxonomy ("internal", "partner_acme", "partner-acme", "public", etc.).
AudienceTag = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$", strip_whitespace=True),
]

# Dotted Python path with a `:` separator for the attribute (class) within
# the module. Example: "devices.acme_edge_cam.handlers.media:ListFiles".
_DOTTED_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_dotted_path(value: str) -> str:
    if not _DOTTED_PATH_RE.match(value):
        raise ValueError(
            f"invalid dotted path {value!r}: expected 'package.module:ClassName' "
            "(module path, then ':', then attribute name)"
        )
    return value


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class _ManifestModel(BaseModel):
    """Shared config for every manifest model."""

    model_config = ConfigDict(
        extra="forbid",  # surface typos in YAML loudly
        frozen=True,
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class Device(_ManifestModel):
    """Metadata for the target device."""

    id: IdentifierStr
    display_name: str = Field(min_length=1)
    firmware_min: str | None = None
    audience: list[AudienceTag] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class TransportAuth(_ManifestModel):
    """Optional per-transport handshake recipe for session-required commands."""

    handler: str

    @field_validator("handler")
    @classmethod
    def _check_handler(cls, value: str) -> str:
        return _validate_dotted_path(value)


class BleChannelSpec(_ManifestModel):
    """One named (write, notify) characteristic pair on a BLE transport.

    Channels live on the *transport*; commands select one via
    ``ble.<transport_id>.channel``.
    """

    write: str = Field(min_length=1)
    notify: str = Field(min_length=1)


class Transport(_ManifestModel):
    """A wire transport (BLE / HTTP / serial / etc.) plus its codec.

    The optional ``family`` field tags the transport for cross-validation:
    when set, commands riding this transport must supply the matching
    per-transport block (``http:`` for ``family=http``, ``ble:`` for
    ``family=ble``). Loopback / unknown / null transports impose no such
    requirement (this preserves the lightweight test-fixture style used
    in unit tests).
    """

    id: IdentifierStr
    adapter: str | None = None
    codec: str
    family: Literal["loopback", "http", "ble"] | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    channels: dict[IdentifierStr, BleChannelSpec] = Field(default_factory=dict)
    auth: TransportAuth | None = None

    @field_validator("adapter", "codec")
    @classmethod
    def _check_paths(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_dotted_path(value)

    @model_validator(mode="after")
    def _check_family_consistency(self) -> Transport:
        if self.channels and self.family != "ble":
            raise ValueError(
                f"transport {self.id!r} declares channels but family is "
                f"{self.family!r}; channels are only valid when family='ble'"
            )
        return self


# ---------------------------------------------------------------------------
# Per-transport command behavior blocks
# ---------------------------------------------------------------------------


class HttpCommandSpec(_ManifestModel):
    """Per-(command, http-transport) wire-format and behavior block."""

    method: Literal["GET", "POST", "PUT", "DELETE"]
    path: str = Field(min_length=1)
    body_codec: Literal["json", "none"] = "json"
    response_codec: Literal["json", "none"] = "json"
    query_from_request: bool = False
    expects_response: bool = True
    timeout: float | None = Field(default=None, gt=0)


class BleCommandSpec(_ManifestModel):
    """Per-(command, ble-transport) channel routing and behavior block.

    ``channel`` must name a channel declared on the BLE transport's
    ``channels:`` map.
    """

    channel: IdentifierStr
    expects_response: bool = True
    timeout: float | None = Field(default=None, gt=0)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(_ManifestModel):
    """One request/response operation against the device."""

    id: IdentifierStr
    # Required field that *may* be explicitly null to select the default handler.
    handler: str | None
    transports: list[IdentifierStr] = Field(min_length=1)
    audience: list[AudienceTag] = Field(min_length=1)
    opcode: int | str | None = None
    capabilities: list[str] = Field(default_factory=list)
    idempotent: bool = False
    timeout: float | None = Field(default=None, gt=0)
    retries: int = Field(default=0, ge=0)
    session_required: bool = False
    # Per-transport behavior blocks. Keys are transport ids that
    # must appear in `transports` above and belong to the matching family.
    http: dict[IdentifierStr, HttpCommandSpec] = Field(default_factory=dict)
    ble: dict[IdentifierStr, BleCommandSpec] = Field(default_factory=dict)

    @field_validator("handler")
    @classmethod
    def _check_handler(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_dotted_path(value)

    @model_validator(mode="after")
    def _check_retries_idempotent(self) -> Command:
        """A retry budget is only honored for idempotent commands, so require the pairing."""
        if self.retries > 0 and not self.idempotent:
            raise ValueError(
                f"command {self.id!r}: retries={self.retries} requires 'idempotent: true' "
                "(auto-resending a non-idempotent command risks double execution)"
            )
        return self


# ---------------------------------------------------------------------------
# State + emission primitives: attributes (read/write/subscribe) and events
# (subscribe-only). Both share the HTTP subscribe wiring below.
# ---------------------------------------------------------------------------


def _check_subscribe_interval(mode: str, interval: float | None, *, what: str) -> None:
    """Enforce the poll/sse interval contract shared by attribute + event subscribe wiring."""
    if mode == "poll" and interval is None:
        raise ValueError(f"{what} mode='poll' requires an 'interval' (seconds)")
    if mode == "sse" and interval is not None:
        raise ValueError(f"{what} 'interval' only applies to mode='poll'")


class HttpAttributeOp(_ManifestModel):
    """One HTTP read/write operation for an attribute — a command-shaped block.

    Verbs are explicit, never assumed: a non-REST device that *writes* via
    ``GET`` (e.g. ``GET /setting?option=9``) sets ``method: GET`` with
    ``query_from_request: true``, exactly like a command.
    """

    method: Literal["GET", "POST", "PUT", "DELETE"] = "GET"
    path: str = Field(min_length=1)
    body_codec: Literal["json", "none"] = "json"
    response_codec: Literal["json", "none"] = "json"
    query_from_request: bool = False
    timeout: float | None = Field(default=None, gt=0)


class HttpAttributeSubscribe(_ManifestModel):
    """HTTP subscribe wiring for an attribute: native SSE, or explicit opt-in polling.

    Polling is never a surprise default — ``mode: poll`` must supply an
    ``interval``. ``mode: sse`` opens one long-lived event stream instead.
    """

    mode: Literal["sse", "poll"] = "sse"
    path: str = Field(min_length=1)
    interval: float | None = Field(default=None, gt=0)
    timeout: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_interval(self) -> HttpAttributeSubscribe:
        _check_subscribe_interval(self.mode, self.interval, what="http attribute subscribe")
        return self


class HttpAttributeSpec(_ManifestModel):
    """Per-(attribute, http-transport) wiring: the ops this attribute supports here."""

    read: HttpAttributeOp | None = None
    write: HttpAttributeOp | None = None
    subscribe: HttpAttributeSubscribe | None = None


class BleAttributeSpec(_ManifestModel):
    """Per-(attribute, ble-transport) wiring: one channel carries read/write/notify."""

    channel: IdentifierStr
    timeout: float | None = Field(default=None, gt=0)


class HttpEventSpec(_ManifestModel):
    """Per-(event, http-transport) subscribe wiring: native SSE, or explicit opt-in polling.

    Identical shape to an attribute's subscribe block — an event *is* a
    subscribe-only stream — but kept as its own model so the two primitives stay
    self-documenting. Polling is never a surprise default: ``mode: poll`` must
    supply an ``interval``; ``mode: sse`` opens one long-lived event stream.
    """

    mode: Literal["sse", "poll"] = "sse"
    path: str = Field(min_length=1)
    interval: float | None = Field(default=None, gt=0)
    timeout: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_interval(self) -> HttpEventSpec:
        _check_subscribe_interval(self.mode, self.interval, what="http event subscribe")
        return self


class BleEventSpec(_ManifestModel):
    """Per-(event, ble-transport) subscribe wiring: one channel's notify stream."""

    channel: IdentifierStr
    timeout: float | None = Field(default=None, gt=0)


class Attribute(_ManifestModel):
    """Named device-state primitive: read / write / subscribe.

    The shape is accepted so authors and editors can work with it, but the
    runtime rejects manifests that declare attributes until support lands
    (see :func:`_check_reserved_primitives`).
    """

    id: IdentifierStr
    handler: str | None
    transports: list[IdentifierStr] = Field(min_length=1)
    operations: list[Literal["read", "write", "subscribe"]] = Field(min_length=1)
    audience: list[AudienceTag] = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    http: dict[IdentifierStr, HttpAttributeSpec] = Field(default_factory=dict)
    ble: dict[IdentifierStr, BleAttributeSpec] = Field(default_factory=dict)

    @field_validator("handler")
    @classmethod
    def _check_handler(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dotted_path(value)


class Event(_ManifestModel):
    """Stateless device emission primitive: a subscribe-only stream.

    Distinct from an attribute subscribe (which streams changes of a named,
    read/writable *value*): an event has no stored state — it is a
    fire-and-forget emission (e.g. "button pressed", "recording started"). The
    handler declares a single ``payload`` type; the generated facade exposes
    ``client.<namespace>.<event>.subscribe()`` (async-only).
    """

    id: IdentifierStr
    handler: str | None
    transports: list[IdentifierStr] = Field(min_length=1)
    audience: list[AudienceTag] = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    http: dict[IdentifierStr, HttpEventSpec] = Field(default_factory=dict)
    ble: dict[IdentifierStr, BleEventSpec] = Field(default_factory=dict)

    @field_validator("handler")
    @classmethod
    def _check_handler(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dotted_path(value)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


# RFC-4122 UUID, case-insensitive, with hyphens.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class BleDiscoverySpec(_ManifestModel):
    """Match criteria for a BLE advertisement (all fields optional, AND'd).

    The generated SDK emits a ``default_ble_matcher(candidate)`` that returns
    ``True`` only when every present criterion matches the advertisement.
    """

    name_prefix: str | None = Field(default=None, min_length=1)
    service_uuids: list[str] = Field(default_factory=list)
    # Bluetooth SIG company identifier (0..0xFFFF).
    manufacturer_id: int | None = Field(default=None, ge=0, le=0xFFFF)

    @field_validator("service_uuids")
    @classmethod
    def _check_uuids(cls, value: list[str]) -> list[str]:
        for uuid in value:
            if not _UUID_RE.match(uuid):
                raise ValueError(
                    f"invalid service UUID {uuid!r}: expected canonical "
                    "8-4-4-4-12 hex form (e.g. 'b5f90001-aa8d-11e3-9046-0002a5d5c51b')"
                )
        return value


class HttpDiscoverySpec(_ManifestModel):
    """Probe-list configuration for HTTP discovery.

    Unlike BLE, HTTP discovery has nothing to listen for — the scanner
    must be told which URLs to probe. ``base_urls`` is therefore
    required whenever this sub-block is present.
    """

    base_urls: list[str] = Field(min_length=1)
    probe_path: str = Field(default="/", min_length=1)
    server_header_prefix: str | None = Field(default=None, min_length=1)

    @field_validator("base_urls")
    @classmethod
    def _check_base_urls(cls, value: list[str]) -> list[str]:
        for url in value:
            if not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError(
                    f"invalid base_url {url!r}: must start with 'http://' or 'https://'"
                )
        return value


class DiscoverySpec(_ManifestModel):
    """Optional top-level discovery configuration.

    Lists at least one transport-family sub-block. Each present sub-block
    drives generation of a corresponding ``make_<family>_scanner()`` and
    ``default_<family>_matcher()`` in the SDK's ``scanners.py`` module.
    """

    ble: BleDiscoverySpec | None = None
    http: HttpDiscoverySpec | None = None

    @model_validator(mode="after")
    def _check_not_empty(self) -> DiscoverySpec:
        if self.ble is None and self.http is None:
            raise ValueError(
                "discovery: at least one transport family (`ble` or `http`) must be specified"
            )
        return self


# ---------------------------------------------------------------------------
# Vendoring overrides
# ---------------------------------------------------------------------------


class Vendoring(_ManifestModel):
    """Explicit overrides for the import-closure walker.

    The walker auto-discovers most files; these knobs handle edge cases
    (dynamic imports, intentionally vendored extras, exclusions).
    """

    extra_include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level manifest
# ---------------------------------------------------------------------------


CURRENT_SCHEMA_VERSION = 1


class Manifest(_ManifestModel):
    """Top-level manifest: one device, its transports, and its operations."""

    schema_version: int
    device: Device
    source_roots: list[str] = Field(min_length=1)
    transports: list[Transport] = Field(min_length=1)
    discovery: DiscoverySpec | None = None
    commands: list[Command] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    vendoring: Vendoring = Field(default_factory=Vendoring)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        if value != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {value}; this generator understands {CURRENT_SCHEMA_VERSION}"
            )
        return value

    @model_validator(mode="after")
    def _check_cross_refs(self) -> Manifest:
        """Verify intra-manifest references are consistent.

        - Every `command.transports[*]` must name a defined transport.
        - Transport ids must be unique.
        - Command / attribute / event ids must be unique within their section.
        - Every command / attribute / event audience must be a subset of
          `device.audience` — you can't ship an operation to an audience the
          device isn't built for.
        - At least one operation (command) must be defined — empty manifests
          are almost always a mistake.
        """
        transport_ids = {t.id for t in self.transports}
        if len(transport_ids) != len(self.transports):
            dupes = _find_duplicates([t.id for t in self.transports])
            raise ValueError(f"duplicate transport ids: {sorted(dupes)}")
        transports_by_id = {t.id: t for t in self.transports}

        device_audiences = set(self.device.audience)
        _check_ids_and_audiences(self.commands, self.attributes, self.events, device_audiences)

        _check_attribute_wiring(self.attributes, transports_by_id)
        _check_event_wiring(self.events, transports_by_id)

        for cmd in self.commands:
            unknown = set(cmd.transports) - transport_ids
            if unknown:
                raise ValueError(
                    f"command {cmd.id!r} references undefined transport(s): {sorted(unknown)}"
                )
            if cmd.handler is None:
                raise ValueError(
                    f"command {cmd.id!r} has handler=null; synthesized default handlers "
                    "are not yet implemented — provide an explicit handler dotted path"
                )
            _check_per_transport_blocks(cmd, transports_by_id)

        if not self.commands and not self.attributes and not self.events:
            raise ValueError("manifest defines no commands, attributes, or events")

        _check_discovery_against_transports(self.discovery, transports_by_id)

        return self


def _find_duplicates(items: list[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for item in items:
        if item in seen:
            dupes.add(item)
        seen.add(item)
    return dupes


def _check_ids_and_audiences(
    commands: list[Command],
    attributes: list[Attribute],
    events: list[Event],
    device_audiences: set[str],
) -> None:
    """Ids unique within *and* across sections; every audience ⊆ device.audience."""
    for section_name, items in (("commands", commands), ("attributes", attributes), ("events", events)):
        ids = [item.id for item in items]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate ids in {section_name}: {sorted(_find_duplicates(ids))}")
        for item in items:
            undeclared = set(item.audience) - device_audiences
            if undeclared:
                raise ValueError(
                    f"{section_name[:-1]} {item.id!r} targets audience(s) {sorted(undeclared)} "
                    f"not declared in device.audience={sorted(device_audiences)}"
                )
    # A command, attribute, and event that share a dotted id would all map to the
    # same ``client.<namespace>.<name>`` facade slot and silently shadow.
    cross_ids = [c.id for c in commands] + [a.id for a in attributes] + [e.id for e in events]
    if len(set(cross_ids)) != len(cross_ids):
        raise ValueError(
            f"ids must be unique across commands/attributes/events; duplicates: {sorted(_find_duplicates(cross_ids))}"
        )


def _check_event_wiring(events: list[Event], transports: dict[str, Transport]) -> None:
    """Validate every event's transports and per-transport subscribe wiring."""
    for event in events:
        _check_event_blocks(event, transports)


def _check_event_blocks(event: Event, transports: dict[str, Transport]) -> None:
    """Validate one event's http/ble subscribe blocks against its transports."""
    unknown = set(event.transports) - set(transports)
    if unknown:
        raise ValueError(f"event {event.id!r} references undefined transport(s): {sorted(unknown)}")
    for tid in event.http:
        _check_block_family("event", event.id, event.transports, tid, "http", transports)
    for tid, ble_spec in event.ble.items():
        _check_block_family("event", event.id, event.transports, tid, "ble", transports)
        channels = transports[tid].channels
        if ble_spec.channel not in channels:
            raise ValueError(
                f"event {event.id!r}: ble.{tid}.channel={ble_spec.channel!r} is not declared in "
                f"transport {tid!r} channels={sorted(channels)}"
            )
    for tid in event.transports:
        family = transports[tid].family
        if family == "http" and tid not in event.http:
            raise ValueError(f"event {event.id!r} rides http transport {tid!r} but has no http.{tid} block")
        if family == "ble" and tid not in event.ble:
            raise ValueError(f"event {event.id!r} rides ble transport {tid!r} but has no ble.{tid} block")


def _check_attribute_wiring(attributes: list[Attribute], transports: dict[str, Transport]) -> None:
    """Validate every attribute's transports and per-transport operation wiring."""
    for attr in attributes:
        _check_attribute_blocks(attr, transports)


def _check_attribute_blocks(attr: Attribute, transports: dict[str, Transport]) -> None:
    """Validate one attribute's http/ble blocks against its transports + operations."""
    unknown = set(attr.transports) - set(transports)
    if unknown:
        raise ValueError(f"attribute {attr.id!r} references undefined transport(s): {sorted(unknown)}")
    for tid in attr.http:
        _check_block_family("attribute", attr.id, attr.transports, tid, "http", transports)
    for tid, ble_spec in attr.ble.items():
        _check_block_family("attribute", attr.id, attr.transports, tid, "ble", transports)
        channels = transports[tid].channels
        if ble_spec.channel not in channels:
            raise ValueError(
                f"attribute {attr.id!r}: ble.{tid}.channel={ble_spec.channel!r} is not declared in "
                f"transport {tid!r} channels={sorted(channels)}"
            )
    ops: set[str] = {str(op) for op in attr.operations}
    for tid in attr.transports:
        family = transports[tid].family
        if family == "http":
            _check_http_attribute_ops(attr, tid, ops)
        elif family == "ble" and tid not in attr.ble:
            raise ValueError(f"attribute {attr.id!r} rides ble transport {tid!r} but has no ble.{tid} block")


def _check_block_family(
    kind: str,
    item_id: str,
    item_transports: list[str],
    tid: str,
    family: str,
    transports: dict[str, Transport],
) -> None:
    """Verify a per-transport wiring block names a declared transport of ``family``."""
    if tid not in item_transports:
        raise ValueError(
            f"{kind} {item_id!r}: {family} block names transport {tid!r} which is not in "
            f"transports={item_transports}"
        )
    if transports[tid].family != family:
        raise ValueError(
            f"{kind} {item_id!r}: {family} block on transport {tid!r} but family is "
            f"{transports[tid].family!r}"
        )


def _check_http_attribute_ops(attr: Attribute, tid: str, ops: set[str]) -> None:
    """Ensure each declared operation has a matching entry in the http block."""
    block = attr.http.get(tid)
    if block is None:
        raise ValueError(f"attribute {attr.id!r} rides http transport {tid!r} but has no http.{tid} block")
    wired = {"read": block.read, "write": block.write, "subscribe": block.subscribe}
    for op in sorted(ops):
        if wired[op] is None:
            raise ValueError(
                f"attribute {attr.id!r}: operation {op!r} is declared but http.{tid}.{op} is missing"
            )


def _check_http_block(cmd: Command, transports: dict[str, Transport]) -> None:
    """Validate each entry in ``cmd.http`` against the transport map."""
    for tid in cmd.http:
        if tid not in cmd.transports:
            raise ValueError(
                f"command {cmd.id!r}: http block names transport {tid!r} which is not in "
                f"transports={cmd.transports}"
            )
        family = transports[tid].family
        if family != "http":
            raise ValueError(
                f"command {cmd.id!r}: http block on transport {tid!r} but family is {family!r}"
            )


def _check_ble_block(cmd: Command, transports: dict[str, Transport]) -> None:
    """Validate each entry in ``cmd.ble`` against the transport map."""
    for tid, ble_spec in cmd.ble.items():
        if tid not in cmd.transports:
            raise ValueError(
                f"command {cmd.id!r}: ble block names transport {tid!r} which is not in "
                f"transports={cmd.transports}"
            )
        transport = transports[tid]
        if transport.family != "ble":
            raise ValueError(
                f"command {cmd.id!r}: ble block on transport {tid!r} but family is "
                f"{transport.family!r}"
            )
        if ble_spec.channel not in transport.channels:
            raise ValueError(
                f"command {cmd.id!r}: ble.{tid}.channel={ble_spec.channel!r} is not declared in "
                f"transport {tid!r} channels={sorted(transport.channels)}"
            )


def _check_per_transport_blocks(cmd: Command, transports: dict[str, Transport]) -> None:
    """Enforce HTTP/BLE block consistency for a single command.

    Rules:
    - Keys of ``cmd.http`` must appear in ``cmd.transports`` and reference
      a transport with ``family='http'``.
    - Keys of ``cmd.ble`` must appear in ``cmd.transports``, reference a
      transport with ``family='ble'``, and the named channel must exist
      on that transport's ``channels:`` map.
    - When a transport in ``cmd.transports`` has ``family='http'`` /
      ``family='ble'``, the matching block must be present. Loopback /
      unknown / null families impose no such requirement.
    """
    _check_http_block(cmd, transports)
    _check_ble_block(cmd, transports)

    for tid in cmd.transports:
        transport = transports[tid]
        if transport.family == "http" and tid not in cmd.http:
            raise ValueError(
                f"command {cmd.id!r} rides http transport {tid!r} but has no http.{tid} block"
            )
        if transport.family == "ble" and tid not in cmd.ble:
            raise ValueError(
                f"command {cmd.id!r} rides ble transport {tid!r} but has no ble.{tid} block"
            )


def _check_discovery_against_transports(
    discovery: DiscoverySpec | None, transports: dict[str, Transport]
) -> None:
    """Each declared discovery sub-block must have a matching transport family."""
    if discovery is None:
        return
    families = {t.family for t in transports.values()}
    if discovery.ble is not None and "ble" not in families:
        raise ValueError(
            "discovery.ble is declared but no transport has family='ble'"
        )
    if discovery.http is not None and "http" not in families:
        raise ValueError(
            "discovery.http is declared but no transport has family='http'"
        )
