# Design Review & Decision Log

This page preserves the early architectural decisions and the reasoning behind them, captured while
Kandra's shape was being locked in. The *behavior* each decision produced is documented on the
individual concept pages; this page is the **"why"**, kept for future maintainers who ask "why does
it work this way?"

Everything here is genericized: Kandra is IP-isolated, and so is its design history. The concrete
examples use the fictional [Pneumatic Bear Poker](concepts/manifest.md) reference device.

---

## Decision log

- **Project scope captured.** Three-layer architecture (authoring inputs → runtime → generated SDK),
  async-first, with audience as a first-class concept.

- **Pivoted from schema-first to code-first.** The YAML manifest is *wiring only*; Python is the
  source of truth for types and behavior. The generator vendors the transitive import closure into
  `dist/`. Multi-language (TypeScript/C) support was dropped from scope — a code-first design means the
  source of truth *is* Python, and a parallel language would require a schema-first track.

- **`handler` is required; explicit `null` selects the default handler.** No implicit fallback. (The
  schema still permits `null` for forward compatibility with synthesized defaults, but the loader
  rejects it today with a clear "not yet implemented" message.)

- **Reviewed prior art.** Typed HTTP+BLE SDK *generation* is an unoccupied niche apart from the CSA
  Matter ecosystem.

- **Dynamic imports must be declared.** Any `importlib.import_module(...)` inside a
  handler/transport/codec must list its target in `vendoring.extra_include`, or the import-closure
  build fails. Explicit-and-loud beats clever-and-silent.

- **Attributes, then events, reserved as primitives — deferred.** The manifest accepted
  `attributes:` / `events:` blocks from early on; the runtime behavior landed later (M9). HTTP-polling
  fallback for attribute-subscribe is explicit opt-in per attribute, never a default.

- **Open questions resolved.** Events are a third top-level primitive (distinct from
  attribute-subscribe). Stateful sessions are modeled as a `session_required` command flag plus a
  per-transport `auth:` block. Saved-device state lives behind a pluggable `IdentityStore` protocol
  with an XDG-JSON default. Logging uses the standard library for now. Versioning is fully manual
  semver; `_generated_from.json` records provenance but does not derive the version.

- **Handler contract.** Each handler class exposes plain class attributes `request = SomeRequest` and
  `response = SomeResponse`. The generator reads `cls.request` / `cls.response` at build time to wire
  codec types and emit typed facade methods — no reflection on annotations.

- **Code generator landed.** `kandra build` emits a client package (`<device_id>_sdk/`) with a
  `TransportId` enum, a command registry mapping each command/transport pair to a runtime `Command`,
  and a typed client facade (`client.<namespace>.<method>(request, *, via=...)`).

- **Post-M2 design review.** Nine questions raised against the minimalist `Transport` protocol; the
  outcomes are captured under [Post-M2 design review](#post-m2-design-review) below. Headline results:
  `Transport` became generic on a per-family wire envelope; retries became a wrapper; `expects_response`
  became a first-class flag; a `Result[T]` / `Classification` envelope was adopted; discovery,
  enrollment, and identity persistence became first-class; BLE gained named channels.

- **M8 landed (audience pruning + leakage scan + vendoring).** `--profile` *activates* vendoring —
  `kandra build` without a profile keeps the import-from-`source_roots` behavior, so existing consumers
  are unaffected. The namespace rewrite *re-homes* each source root under one `<pkg>/_internal/`
  subpackage (preserving its relative path) rather than flattening. Rewriting is **textual**, so
  comments — critically the `# kandra-audience:` headers — survive verbatim. Two audience-leak guards
  fail loud: a kept file importing a dropped file, and a surviving command whose own handler/model/codec
  was dropped. Leakage matching is case-sensitive substring (mirrors `grep`).

---

## Post-M2 design review

Nine architectural corrections raised after M2 landed. Each stands alone.

### Wire format is manifest-declared, not codec-inferred

An early (wrong) claim was "the codec picks the HTTP method." In fact the method, path, query layout,
and body shape are **wire-format metadata about the command**, not derivable from a typed request
dataclass — so they belong in the manifest. The codec only serializes the body and parses the response
body.

```yaml
commands:
  # GET — query-string args from request fields, no body.
  - id: settings.read
    handler: devices.pneumatic_bear_poker.handlers.settings:ReadIntensity
    transports: [http]
    http:
      http:
        method: GET
        path: /v1/settings/intensity
        query_from_request: true    # request fields -> ?level=X

  # POST — JSON body, JSON response.
  - id: poker.deploy
    handler: devices.pneumatic_bear_poker.handlers.poker:Deploy
    transports: [http]
    http:
      http:
        method: POST
        path: /v1/poker/deploy
```

HTTP verbs are **not** interchangeable — `GET /status` and `POST /status` are different operations to a
server. The transport supports all four verbs (GET/POST/PUT/DELETE) and the manifest picks one per
command. There is no "send everything as POST" mode.

This forced a generalization: `Transport` became `Transport[WireReqT, WireRespT]` and `Codec` became
`Codec[RequestT, ResponseT, WireReqT, WireRespT]`. Loopback stays `Transport[bytes, bytes]`; HTTP is
`Transport[HttpRequest, HttpResponse]`; BLE is `Transport[BleRequest, bytes]`.

### Retries live above the transport, not in the base protocol

Retry policy is deliberately kept out of the base `Transport` protocol, so a "send once, observe the
truth" test mode is just the default rather than a magic flag. Retries are instead a **per-command**
concern declared in the manifest: mark a command `idempotent: true` (re-sending is a safe no-op) and
give it `retries: N`, and `dispatch()` re-sends it up to `N` times after a *transient transport
failure* (dropped link, timeout). Non-idempotent commands are never auto-retried. Protocol-level
backoff (an HTTP 503 or a BLE "busy" ack the device wants you to retry) is **not** automated today —
`dispatch()` returns the classified `Result` and the caller decides whether to re-issue.

### No-response commands are a manifest flag

A fire-and-forget command declares `expects_response: false` (per transport, since a factory-reset may
ack over BLE before the firmware tears down, unlike HTTP). The runtime then:

1. Sends the request.
2. Waits, bounded by `timeout`.
3. **Swallows a `TransportTimeoutError` and treats it as success** — the fire-and-forget contract.
4. Skips `codec.decode` entirely.
5. Requires the handler's `response` attribute to be `None` (enforced at build time).
6. Returns `None` — there is no `Result`, since nothing was classified or decoded.

This replaces a hand-maintained "no-response commands" allow-list with a per-command declarative flag
that both the generator and runtime can see.

### Wire-format quirks are named, manifest-visible overrides

**Principle:** no hidden device-specific behavior in built-in transports. If a quirk exists, it has a
*name*, lives in the device's source tree, and is wired in by the manifest — never by an
`if 'foo' in url` buried in the transport.

- **Per-command codec override.** A command's per-transport wire block may name its own `codec:`
  instead of inheriting the transport default. This is how one transport carries mixed wire formats — a
  JSON status endpoint alongside a raw-bytes media download over the same HTTP transport, or a TLV
  control channel alongside an opaque-payload channel over the same BLE link. For HTTP the override
  replaces the built-in `HttpJsonCodec` (typically a subclass that overrides `decode`); for BLE it
  replaces the transport's payload codec for that command's channel. The override lives in the device's
  source tree, is vendored into the SDK, and is greppable from every direction (codec name → call
  sites; URL path or channel → manifest entry; bug ID → docstring).

- **Header and envelope quirks ride in that same codec.** A codec produces the *entire* wire request —
  for HTTP that includes the method, path, query, and headers — so a "this command needs `Connection:
  close`" quirk is just a field the custom codec sets. There is no separate request-filter concept to
  learn or maintain.

**The five-year-old-bug test.** If a future engineer asks "why does this command behave differently?",
the answer is one manifest-grep plus one source-file open away. No archaeology, and no URL-substring
workaround hidden inside a shared HTTP client.

### Result envelope and classification pipeline

Every dispatched response is wrapped in a `Result[T]` carrying a five-state `Classification`
(`ACCEPTED`, `REJECTED`, `DEVICE_FAULT`, `TRANSPORT_FAILURE`, `ANOMALOUS`). What carried over from
prior art, and what changed:

- A device-specific result type became the **generic `Result[T]`**; a device-specific fault name became
  the neutral `DEVICE_FAULT`.

- Convenience predicates (`accepted`, `rejected`, …) are adopted as-is on `Result[T]`.

- **Fail-fast is opt-in.** The default is `result = await client.poker.deploy(req)`; the caller checks
  `.accepted` or reads `.data`. A framework built on Kandra wires
  `client.on_non_accepted = self.fail_test` itself; an `ignore_failures()` context manager suppresses
  it for negative-path assertions.

- **Universal** classification rules (anomalous; transport failure; HTTP 5xx → fault; HTTP 4xx →
  reject) ship in `kandra_runtime`. **Device-specific** rules (an application status enum, a BLE TLV
  "accepted" flag) live in a user-supplied interpreter plugged in via the `ResponseInterpreter`
  protocol.

### Discovery, enrollment, and identity persistence are first-class

The early scope was too narrow. Kandra ships three sibling protocols alongside `Transport`:

- **`Scanner`** — discover candidate devices over a transport family, yielding `Candidate` records
  whose family-specific fields live in a `details`/metadata map.

- **`Enrollment`** — one-time setup that produces the persistent credentials a transport later needs at
  `open()`. The BLE term "pairing" was generalized to **enrollment** to cover certificate exchange,
  token login, and the no-op case:

  - BLE: pairing / bonding → a bond record.
  - HTTP with certificates → a client cert.
  - HTTP with tokens → a bearer token.
  - Raw HTTP / USB → no-op.

- **`IdentityStore`** — persist enrollment records and connection params across sessions, with an
  XDG-JSON default implementation in the runtime.

The end-user ergonomic is a generated `discover_and_connect()` for the common case, with the explicit
`scan_*()` → `Enrollment.enroll()` → `Client.connect()` flow preserved for custom auth and
bench-disambiguation.

### What the Transport protocol really needs

The earlier `open`/`close`/`request` proposal was too thin. The honest minimum adds `is_open`,
`subscribe` (long-lived notifications — BLE Notify, HTTP SSE — powering attributes and events), and a
deferred `stream` (long byte downloads / bulk transfer, added only when a real use case lands).

What stays **off** `Transport`:

- **Scan / enroll** — sibling protocols, above.

- **Retry / heartbeat** — wrappers or command-level concerns.

- **Liveness** ("is this thing actually responding?") — a higher-level check that issues a known cheap
  command and observes the `Result[T]`. "Responding" is a device-protocol question, not a wire
  question: link-connected ≠ device-responsive.

### BLE: one transport per device, named channels per command

An early proposal of "one `BleTransport` per write/notify characteristic pair" was wrong — it would
force end users to juggle multiple transport objects for a single device. The corrected design:

- **One `BleTransport` per physical device connection**, owning one client.

- **Channels are named on the transport** — each a `(write_uuid, notify_uuid)` pair declared in the
  manifest.

- **Commands name the channel they ride on.** End users never see UUIDs.

```yaml
transports:
  - id: ble
    codec: devices.pneumatic_bear_poker.codecs:TlvCodec
    channels:
      command:  { write: "0000fff1-0000-1000-8000-00805f9b34fb", notify: "0000fff2-0000-1000-8000-00805f9b34fb" }
      query:    { write: "0000fff3-0000-1000-8000-00805f9b34fb", notify: "0000fff4-0000-1000-8000-00805f9b34fb" }

commands:
  - id: poker.deploy
    handler: devices.pneumatic_bear_poker.handlers.poker:Deploy
    transports: [ble]
    ble:
      channel: command
```

The BLE wire envelope carries the channel selector (`BleRequest(channel, payload)`), yet end-user code
is identical to HTTP: `await client.poker.deploy(req)` routes via the named channel. HTTP has **no**
channel concept — method + path already discriminate — and the schema validator enforces that
`channels:` / `channel:` appear only on BLE.

### Summary of resulting design changes

| # | Change | Affected layer |
|---|--------|----------------|
| 1 | `Transport` generic on a per-family wire envelope | Runtime + generator + codec protocol |
| 2 | `http:` / `ble:` per-command sub-blocks in the manifest | Manifest schema + generator |
| 3 | `expects_response: false` first-class flag | Manifest + runtime dispatch |
| 4 | Named codec / filter overrides for quirks | Manifest + codec discovery |
| 5 | `Result[T]` + `Classification` envelope | Runtime (new module) |
| 6 | `Scanner`, `Enrollment`, `IdentityStore` protocols | Runtime (new module) |
| 7 | `Transport` grows `is_open`, `subscribe`, `stream` | Runtime |
| 8 | All four HTTP verbs, per-command | Falls out of #1 + #2 |
| 9 | BLE named channels, one transport per device | Manifest + BLE transport |
