"""Pydantic models for the manifest YAML.

These models define the *wiring* of a generated SDK: which device, which transports, which commands, which audiences.
They deliberately do **not** describe Python types or behavior — that's all in user-authored Python, referenced via
dotted paths and resolved at generation time.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# ---------------------------------------------------------------------------
# Primitive types
# ---------------------------------------------------------------------------

# Identifiers used as YAML keys / facade attr names: dotted, snake_case segments. Examples: "media", "media.list_files",
# "settings.resolution".
IdentifierStr = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$", strip_whitespace=True),
]

# Audience tags. Lowercase with `_` or `-` so users can pick their own taxonomy ("internal", "partner_acme",
# "partner-acme", "public", etc.).
AudienceTag = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$", strip_whitespace=True),
]

# Dotted Python path with a `:` separator for the attribute (class) within the module. Example:
# "devices.acme_edge_cam.handlers.media:ListFiles".
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
        use_attribute_docstrings=True,  # field docstrings become schema + autodoc descriptions
    )


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class Device(_ManifestModel):
    """Metadata for the target device."""

    id: IdentifierStr
    """Short snake_case name for the device, e.g. ``pneumatic_bear_poker``. The generated package is ``<id>_sdk``."""
    display_name: str = Field(min_length=1)
    """Human-readable device name, e.g. ``Pneumatic Bear Poker``."""
    firmware_min: str | None = None
    """Oldest firmware version the SDK supports. Recorded only; not checked yet."""
    audience: list[AudienceTag] = Field(min_length=1)
    """Every audience the device is built for, e.g. ``internal``. Each operation's ``audience`` picks from this list."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class TransportAuth(_ManifestModel):
    """Optional per-transport handshake recipe for session-required commands."""

    handler: str
    """Handshake class (``module:Class``) to run before ``session_required`` commands. Reserved; not used yet."""

    @field_validator("handler")
    @classmethod
    def _check_handler(cls, value: str) -> str:
        return _validate_dotted_path(value)


class TransportEnrollment(_ManifestModel):
    """Declarative first-run login the generated SDK bakes into its HTTP enrollment adapter.

    Only the *static* wiring lives here (the login path plus which response field carries the token). Runtime secrets --
    credentials, API keys -- never live in the manifest; callers inject them at runtime via the generated
    ``<family>_enrollment(login_payload=...)`` factory.
    """

    login_path: str = Field(min_length=1)
    """Path the SDK POSTs to in order to log in and get a token, e.g. ``/v1/auth/login``."""
    token_field: str | None = None
    """Field in the login reply that holds the token. Omit to try ``token``, then ``access_token``."""

    @field_validator("login_path")
    @classmethod
    def _check_login_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"login_path must be an absolute path starting with '/', got {value!r}")
        return value


class BleChannelSpec(_ManifestModel):
    """One named (write, notify) characteristic pair on a BLE transport.

    Channels live on the *transport*; commands select one via ``ble.<transport_id>.channel``.
    """

    write: str = Field(min_length=1)
    """UUID of the characteristic the SDK writes requests to."""
    notify: str = Field(min_length=1)
    """UUID of the characteristic the device sends replies on."""


class Transport(_ManifestModel):
    """A wire transport (BLE / HTTP / serial / etc.) plus its codec.

    ``codec`` is the default for every command on the transport (a per-command ``codec:`` overrides it). It is required
    for every family except HTTP, which falls back to the runtime ``HttpJsonCodec`` when ``codec`` is omitted.

    The optional ``family`` field tags the transport for cross-validation: when set, commands riding this transport must
    supply the matching per-transport block (``http:`` for ``family=http``, ``ble:`` for ``family=ble``). Loopback /
    unknown / null transports impose no such requirement (this preserves the lightweight test-fixture style used in unit
    tests).
    """

    id: IdentifierStr
    """Name for this transport, e.g. ``wifi``. Commands, attributes, and events refer to it."""
    adapter: str | None = None
    """Your own transport class (``module:Class``). Omit to use the built-in ``HttpTransport`` or ``BleTransport``."""
    codec: str | None = None
    """Class (``module:Class``) that converts requests and replies to and from the wire, used by every command on this
    transport. Required unless ``family`` is ``http``, which defaults to JSON (``HttpJsonCodec``)."""
    interpreter: str | None = None
    """Class (``module:Class``) that decides whether each reply means success. Omit for the default: HTTP status codes
    for ``http``, every reply accepted otherwise."""
    family: Literal["loopback", "http", "ble"] | None = None
    """Kind of connection. Decides which other fields the transport needs and which wiring block (``http:`` or
    ``ble:``) each operation on it needs."""
    channels: dict[IdentifierStr, BleChannelSpec] = Field(default_factory=dict)
    """BLE only: named pairs of characteristic UUIDs. Each BLE operation picks one by name."""
    auth: TransportAuth | None = None
    """Handshake to run before ``session_required`` commands. Reserved; not used yet."""
    enrollment: TransportEnrollment | None = None
    """HTTP only: the device's login endpoint, built into the SDK's ``http_enrollment()``."""

    @field_validator("adapter", "codec", "interpreter")
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
        if self.codec is None and self.family != "http":
            raise ValueError(
                f"transport {self.id!r}: 'codec' is required for family={self.family!r} "
                "(only HTTP transports may omit it and use the built-in HttpJsonCodec)"
            )
        if self.enrollment is not None and self.family != "http":
            raise ValueError(
                f"transport {self.id!r} declares enrollment but family is {self.family!r}; "
                "enrollment is only supported on HTTP transports"
            )
        return self


# ---------------------------------------------------------------------------
# Per-transport command behavior blocks
# ---------------------------------------------------------------------------


class HttpCommandSpec(_ManifestModel):
    """Per-(command, http-transport) wire-format and behavior block."""

    method: Literal["GET", "POST", "PUT", "DELETE"]
    """HTTP method for this command."""
    path: str = Field(min_length=1)
    """URL path, e.g. ``/v1/poker/deploy``."""
    codec: str | None = None
    """Codec class (``module:Class``) for this one command, replacing the transport's codec. Use it when one command's
    format differs from the rest, such as a binary download; the easy start is a subclass of ``HttpJsonCodec``."""
    query_from_request: bool = False
    """Send the request's fields as URL query parameters instead of a JSON body."""
    expects_response: bool = True
    """Set ``false`` for fire-and-forget commands: a missing reply counts as success and the call returns ``None``."""
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait on this transport. Overrides the command's ``timeout``."""

    @field_validator("codec")
    @classmethod
    def _check_codec(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dotted_path(value)


class BleCommandSpec(_ManifestModel):
    """Per-(command, ble-transport) channel routing and behavior block.

    ``channel`` must name a channel declared on the BLE transport's ``channels:`` map.
    """

    channel: IdentifierStr
    """Name of the transport channel this command uses (one of the transport's ``channels``)."""
    codec: str | None = None
    """Payload codec class (``module:Class``) for this one command, replacing the transport's codec."""
    expects_response: bool = True
    """Set ``false`` for fire-and-forget commands: a missing reply counts as success."""
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait on this transport. Overrides the command's ``timeout``."""

    @field_validator("codec")
    @classmethod
    def _check_codec(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dotted_path(value)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(_ManifestModel):
    """One request/response operation against the device."""

    id: IdentifierStr
    """Dotted name, e.g. ``poker.deploy``, which becomes ``client.poker.deploy()``. Needs at least one dot."""
    # Required field that *may* be explicitly null to select the default handler.
    handler: str | None
    """Your handler class (``module:Class``) that declares the request and response types."""
    transports: list[IdentifierStr] = Field(min_length=1)
    """Ids of the transports this command can use. Each ``http`` or ``ble`` one needs a wiring block below."""
    audience: list[AudienceTag] = Field(min_length=1)
    """Audiences whose SDK includes this command. Each must be listed in ``device.audience``."""
    capabilities: list[str] = Field(default_factory=list)
    """Capability tags the device must report; the client refuses the command on devices without them."""
    idempotent: bool = False
    """``true`` if sending the command twice does no harm. Required for ``retries``."""
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait for a reply. A wiring block's ``timeout`` overrides it for that transport."""
    retries: int = Field(default=0, ge=0)
    """How many times to re-send after a dropped connection or timeout. Requires ``idempotent: true``."""
    session_required: bool = False
    """Run the transport's ``auth`` handshake before this command. Reserved; not used yet."""
    # Per-transport behavior blocks. Keys are transport ids that must appear in `transports` above and belong to the
    # matching family.
    http: dict[IdentifierStr, HttpCommandSpec] = Field(default_factory=dict)
    """Wiring for each HTTP transport, keyed by transport id."""
    ble: dict[IdentifierStr, BleCommandSpec] = Field(default_factory=dict)
    """Wiring for each BLE transport, keyed by transport id."""

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
# State + emission primitives: attributes (read/write/subscribe) and events (subscribe-only). Both share the HTTP
# subscribe wiring below.
# ---------------------------------------------------------------------------


def _check_subscribe_interval(mode: str, interval: float | None, *, what: str) -> None:
    """Enforce the poll/sse interval contract shared by attribute + event subscribe wiring."""
    if mode == "poll" and interval is None:
        raise ValueError(f"{what} mode='poll' requires an 'interval' (seconds)")
    if mode == "sse" and interval is not None:
        raise ValueError(f"{what} 'interval' only applies to mode='poll'")


class HttpAttributeOp(_ManifestModel):
    """One HTTP read/write operation for an attribute — a command-shaped block.

    Verbs are explicit, never assumed: a non-REST device that *writes* via ``GET`` (e.g. ``GET /setting?option=9``) sets
    ``method: GET`` with ``query_from_request: true``, exactly like a command.
    """

    method: Literal["GET", "POST", "PUT", "DELETE"] = "GET"
    """HTTP method for this operation."""
    path: str = Field(min_length=1)
    """URL path, e.g. ``/v1/settings/poke_intensity``."""
    query_from_request: bool = False
    """Send the value's fields as URL query parameters instead of a JSON body."""
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait for a reply."""


class HttpAttributeSubscribe(_ManifestModel):
    """HTTP subscribe wiring for an attribute: native SSE, or explicit opt-in polling.

    Polling is never a surprise default — ``mode: poll`` must supply an ``interval``. ``mode: sse`` opens one long-lived
    event stream instead.
    """

    mode: Literal["sse", "poll"] = "sse"
    """``sse`` keeps one stream open for updates; ``poll`` repeats a request every ``interval`` seconds."""
    path: str = Field(min_length=1)
    """URL path of the stream (``sse``) or of the request to repeat (``poll``)."""
    interval: float | None = Field(default=None, gt=0)
    """Seconds between polls. Required for ``poll``; not allowed for ``sse``."""
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait for each request."""

    @model_validator(mode="after")
    def _check_interval(self) -> HttpAttributeSubscribe:
        _check_subscribe_interval(self.mode, self.interval, what="http attribute subscribe")
        return self


class HttpAttributeSpec(_ManifestModel):
    """Per-(attribute, http-transport) wiring: the ops this attribute supports here."""

    read: HttpAttributeOp | None = None
    """How to read the value. Required when ``operations`` includes ``read``."""
    write: HttpAttributeOp | None = None
    """How to write the value. Required when ``operations`` includes ``write``."""
    subscribe: HttpAttributeSubscribe | None = None
    """How to get updates. Required when ``operations`` includes ``subscribe``."""


class BleAttributeSpec(_ManifestModel):
    """Per-(attribute, ble-transport) wiring: one channel carries read/write/notify."""

    channel: IdentifierStr
    """Name of the transport channel that carries this attribute's reads, writes, and updates."""
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait for a reply."""


class HttpEventSpec(_ManifestModel):
    """Per-(event, http-transport) subscribe wiring: native SSE, or explicit opt-in polling.

    Identical shape to an attribute's subscribe block — an event *is* a subscribe-only stream — but kept as its own
    model so the two primitives stay self-documenting. Polling is never a surprise default: ``mode: poll`` must supply
    an ``interval``; ``mode: sse`` opens one long-lived event stream.
    """

    mode: Literal["sse", "poll"] = "sse"
    """``sse`` keeps one stream open for events; ``poll`` repeats a request every ``interval`` seconds."""
    path: str = Field(min_length=1)
    """URL path of the stream (``sse``) or of the request to repeat (``poll``)."""
    interval: float | None = Field(default=None, gt=0)
    """Seconds between polls. Required for ``poll``; not allowed for ``sse``."""
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait for each request."""

    @model_validator(mode="after")
    def _check_interval(self) -> HttpEventSpec:
        _check_subscribe_interval(self.mode, self.interval, what="http event subscribe")
        return self


class BleEventSpec(_ManifestModel):
    """Per-(event, ble-transport) subscribe wiring: one channel's notify stream."""

    channel: IdentifierStr
    """Name of the transport channel whose notifications carry this event."""
    timeout: float | None = Field(default=None, gt=0)
    """Timeout in seconds."""


class Attribute(_ManifestModel):
    """Named device-state primitive: read / write / subscribe.

    Emitted as ``client.<namespace>.<name>.read() / .write(v) / .subscribe()``.
    """

    id: IdentifierStr
    """Dotted name, e.g. ``settings.poke_intensity``, which becomes ``client.settings.poke_intensity``."""
    handler: str | None
    """Your handler class (``module:Class``) that declares the value type."""
    transports: list[IdentifierStr] = Field(min_length=1)
    """Ids of the transports this attribute can use. Each ``http`` or ``ble`` one needs a wiring block below."""
    operations: list[Literal["read", "write", "subscribe"]] = Field(min_length=1)
    """What callers can do with the value."""
    audience: list[AudienceTag] = Field(min_length=1)
    """Audiences whose SDK includes this attribute. Each must be listed in ``device.audience``."""
    capabilities: list[str] = Field(default_factory=list)
    """Capability tags the device must report; the client refuses the attribute on devices without them."""
    http: dict[IdentifierStr, HttpAttributeSpec] = Field(default_factory=dict)
    """Wiring for each HTTP transport, keyed by transport id, with a block for every listed operation."""
    ble: dict[IdentifierStr, BleAttributeSpec] = Field(default_factory=dict)
    """Wiring for each BLE transport, keyed by transport id."""

    @field_validator("handler")
    @classmethod
    def _check_handler(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dotted_path(value)


class Event(_ManifestModel):
    """Stateless device emission primitive: a subscribe-only stream.

    Distinct from an attribute subscribe (which streams changes of a named, read/writable *value*): an event has no
    stored state — it is a fire-and-forget emission (e.g. "button pressed", "recording started"). The handler declares a
    single ``payload`` type; the generated facade exposes ``client.<namespace>.<event>.subscribe()`` (async-only).
    """

    id: IdentifierStr
    """Dotted name, e.g. ``button.pressed``, which becomes ``client.button.pressed.subscribe()``."""
    handler: str | None
    """Your handler class (``module:Class``) that declares the payload type."""
    transports: list[IdentifierStr] = Field(min_length=1)
    """Ids of the transports this event can use. Each ``http`` or ``ble`` one needs a wiring block below."""
    audience: list[AudienceTag] = Field(min_length=1)
    """Audiences whose SDK includes this event. Each must be listed in ``device.audience``."""
    capabilities: list[str] = Field(default_factory=list)
    """Capability tags the device must report; the client refuses the event on devices without them."""
    http: dict[IdentifierStr, HttpEventSpec] = Field(default_factory=dict)
    """Stream or polling wiring for each HTTP transport, keyed by transport id."""
    ble: dict[IdentifierStr, BleEventSpec] = Field(default_factory=dict)
    """Channel wiring for each BLE transport, keyed by transport id."""

    @field_validator("handler")
    @classmethod
    def _check_handler(cls, value: str | None) -> str | None:
        return None if value is None else _validate_dotted_path(value)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


# RFC-4122 UUID, case-insensitive, with hyphens.
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class BleDiscoverySpec(_ManifestModel):
    """Match criteria for a BLE advertisement (all fields optional, AND'd).

    The generated SDK emits a ``default_ble_matcher(candidate)`` that returns ``True`` only when every present criterion
    matches the advertisement.
    """

    name_prefix: str | None = Field(default=None, min_length=1)
    """Match devices whose advertised name starts with this."""
    service_uuids: list[str] = Field(default_factory=list)
    """Match devices that advertise all of these service UUIDs."""
    # Bluetooth SIG company identifier (0..0xFFFF).
    manufacturer_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    """Match devices with this Bluetooth SIG company ID (0 to 65535)."""

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

    Unlike BLE, HTTP discovery has nothing to listen for — the scanner must be told which URLs to probe. ``base_urls``
    is therefore required whenever this sub-block is present.
    """

    base_urls: list[str] = Field(min_length=1)
    """Addresses to check, e.g. ``http://192.168.1.1:8080``. Each one that answers and matches is a found device."""
    probe_path: str = Field(default="/", min_length=1)
    """Path requested at each address to confirm the device, e.g. ``/v1/status``. Must work without logging in."""
    server_header_prefix: str | None = Field(default=None, min_length=1)
    """If set, the reply's ``Server`` header must start with this."""

    @field_validator("base_urls")
    @classmethod
    def _check_base_urls(cls, value: list[str]) -> list[str]:
        for url in value:
            if not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError(f"invalid base_url {url!r}: must start with 'http://' or 'https://'")
        return value


class DiscoverySpec(_ManifestModel):
    """Optional top-level discovery configuration.

    Lists at least one transport-family sub-block. Each present sub-block drives generation of a corresponding
    ``make_<family>_scanner()`` and ``default_<family>_matcher()`` in the SDK's ``scanners.py`` module.
    """

    ble: BleDiscoverySpec | None = None
    """How ``scan_ble()`` recognizes the device."""
    http: HttpDiscoverySpec | None = None
    """Where ``scan_http()`` looks for the device and how it confirms it."""

    @model_validator(mode="after")
    def _check_not_empty(self) -> DiscoverySpec:
        if self.ble is None and self.http is None:
            raise ValueError("discovery: at least one transport family (`ble` or `http`) must be specified")
        return self


# ---------------------------------------------------------------------------
# Vendoring overrides
# ---------------------------------------------------------------------------


class Vendoring(_ManifestModel):
    """Explicit overrides for the import-closure walker.

    The walker auto-discovers most files; these knobs handle edge cases (dynamic imports, intentionally vendored extras,
    exclusions).
    """

    extra_include: list[str] = Field(default_factory=list)
    """Files, folders, or globs (relative to a source root) to bundle even though no code imports them."""
    exclude: list[str] = Field(default_factory=list)
    """Files, folders, or globs (relative to a source root) to leave out of the SDK."""


# ---------------------------------------------------------------------------
# Top-level manifest
# ---------------------------------------------------------------------------


CURRENT_SCHEMA_VERSION = 1


class Manifest(_ManifestModel):
    """Top-level manifest: one device, its transports, and its operations."""

    schema_version: int
    """Manifest format version. Always ``1``."""
    device: Device
    """The device's id, name, and audiences."""
    source_roots: list[str] = Field(min_length=1)
    """Folders, relative to the manifest, that hold your handler, codec, and other Python classes."""
    transports: list[Transport] = Field(min_length=1)
    """How the SDK reaches the device."""
    discovery: DiscoverySpec | None = None
    """How the SDK's ``scan_http()`` / ``scan_ble()`` helpers find the device."""
    commands: list[Command] = Field(default_factory=list)
    """One-shot actions, such as ``client.poker.deploy()``."""
    attributes: list[Attribute] = Field(default_factory=list)
    """Device state you read, write, or subscribe to."""
    events: list[Event] = Field(default_factory=list)
    """Things the device emits that you subscribe to."""
    vendoring: Vendoring = Field(default_factory=Vendoring)
    """Extra files to bundle into the SDK, or to leave out (``--profile`` builds only)."""

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        if value != CURRENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}; this generator understands {CURRENT_SCHEMA_VERSION}")
        return value

    @model_validator(mode="after")
    def _check_cross_refs(self) -> Manifest:
        """Verify intra-manifest references are consistent.

        - Every `command.transports[*]` must name a defined transport.
        - Transport ids must be unique.
        - Command / attribute / event ids must be unique within their section.
        - Every command / attribute / event audience must be a subset of `device.audience` — you can't ship an
          operation to an audience the device isn't built for.
        - At least one operation (command) must be defined — empty manifests are almost always a mistake.
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
                raise ValueError(f"command {cmd.id!r} references undefined transport(s): {sorted(unknown)}")
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
    # A command, attribute, and event that share a dotted id would all map to the same ``client.<namespace>.<name>``
    # facade slot and silently shadow.
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
            f"{kind} {item_id!r}: {family} block on transport {tid!r} but family is " f"{transports[tid].family!r}"
        )


def _check_http_attribute_ops(attr: Attribute, tid: str, ops: set[str]) -> None:
    """Ensure each declared operation has a matching entry in the http block."""
    block = attr.http.get(tid)
    if block is None:
        raise ValueError(f"attribute {attr.id!r} rides http transport {tid!r} but has no http.{tid} block")
    wired = {"read": block.read, "write": block.write, "subscribe": block.subscribe}
    for op in sorted(ops):
        if wired[op] is None:
            raise ValueError(f"attribute {attr.id!r}: operation {op!r} is declared but http.{tid}.{op} is missing")


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
            raise ValueError(f"command {cmd.id!r}: http block on transport {tid!r} but family is {family!r}")


def _check_ble_block(cmd: Command, transports: dict[str, Transport]) -> None:
    """Validate each entry in ``cmd.ble`` against the transport map."""
    for tid, ble_spec in cmd.ble.items():
        if tid not in cmd.transports:
            raise ValueError(
                f"command {cmd.id!r}: ble block names transport {tid!r} which is not in " f"transports={cmd.transports}"
            )
        transport = transports[tid]
        if transport.family != "ble":
            raise ValueError(
                f"command {cmd.id!r}: ble block on transport {tid!r} but family is " f"{transport.family!r}"
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
      transport with ``family='ble'``, and the named channel must exist on that transport's ``channels:`` map.
    - When a transport in ``cmd.transports`` has ``family='http'`` /
      ``family='ble'``, the matching block must be present. Loopback / unknown / null families impose no such
      requirement.
    """
    _check_http_block(cmd, transports)
    _check_ble_block(cmd, transports)

    for tid in cmd.transports:
        transport = transports[tid]
        if transport.family == "http" and tid not in cmd.http:
            raise ValueError(f"command {cmd.id!r} rides http transport {tid!r} but has no http.{tid} block")
        if transport.family == "ble" and tid not in cmd.ble:
            raise ValueError(f"command {cmd.id!r} rides ble transport {tid!r} but has no ble.{tid} block")


def _check_discovery_against_transports(discovery: DiscoverySpec | None, transports: dict[str, Transport]) -> None:
    """Each declared discovery sub-block must have a matching transport family."""
    if discovery is None:
        return
    families = {t.family for t in transports.values()}
    if discovery.ble is not None and "ble" not in families:
        raise ValueError("discovery.ble is declared but no transport has family='ble'")
    if discovery.http is not None and "http" not in families:
        raise ValueError("discovery.http is declared but no transport has family='http'")
