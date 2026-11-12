# Design Decisions

Load-bearing design rules for Kandra, each with the reason it exists. Read this before changing the runtime protocols,
the manifest schema, or generator output. Breaking one of these is a design change, not a refactor — raise it first.

This file is deliberately outside `docs/`, so it is not published on the Sphinx site; concept pages document the
resulting *behavior*, this file records the *why*.

---

## Manifest and authoring

- **Code-first, not schema-first.** Python classes are the source of truth; the manifest is wiring only (dotted-path
  references plus per-transport blocks). Never put type definitions in YAML. *Why:* a schema-first track would need a
  parallel type system. Python-only output is the accepted consequence.

- **Handler contract is plain class attributes.** Each handler exposes `request = SomeRequest` and
  `response = SomeResponse`; the generator reads `cls.request` / `cls.response` at build time. No reflection on
  annotations. *Why:* explicit and trivially readable at build time.

- **`handler` is required.** Explicit `null` is reserved for synthesized default handlers, which the loader rejects
  today with a clear "not yet implemented" error. *Why:* no implicit fallback.

- **Wire format is manifest metadata, not codec-inferred.** HTTP method, path, and query layout (and the BLE channel)
  are declared per command. The codec only serializes the body and parses the response. *Why:* verbs are not
  interchangeable (`GET /status` ≠ `POST /status`) and cannot be derived from a request dataclass. All four verbs are
  supported; there is no "send everything as POST" mode.

- **A transport's `codec:` is the default for every command on it, in every family.** A per-command `codec:`
  overrides it; only HTTP may omit it (built-in `HttpJsonCodec`). *Why:* a device whose whole API uses one non-JSON
  format declares it once, and a codec named in the manifest is never silently ignored.

- **Channels are BLE-only.** `channels:` / `channel:` are rejected on HTTP by the validator. *Why:* method + path
  already discriminate HTTP commands.

- **Dynamic imports must be declared.** Any `importlib.import_module(...)` in a handler, transport, or codec must list
  its target in `vendoring.extra_include`, or the import-closure build fails. *Why:* explicit-and-loud beats
  clever-and-silent.

---

## Runtime protocols

- **`Transport` is generic on a per-family wire envelope.** `Transport[WireReqT, WireRespT]` and
  `Codec[RequestT, ResponseT, WireReqT, WireRespT]`. Loopback is `[bytes, bytes]`, HTTP is
  `[HttpRequest, HttpResponse]`, BLE is `[BleRequest, bytes]`.

- **Keep `Transport` minimal.** It owns `open` / `close` / `is_open` / `request` / `subscribe`. These stay *off* it:

  - Scan / enroll / persistence — sibling protocols `Scanner`, `Enrollment`, `IdentityStore`.
  - Retry and heartbeat — command-level or wrapper concerns.
  - Liveness — issue a cheap command and inspect the `Result`. *Why:* link-connected ≠ device-responsive.
  - HTTP polling — an opt-in wrapper (repeated `request()`), never a transport primitive, so it never turns on by
    surprise.

- **`stream()` stays deferred.** Bulk byte downloads get a protocol method only when a real feature consumes them.
  *Why:* don't grow the protocol speculatively.

- **Retries live in `dispatch()`, per command.** Only `idempotent: true` commands with `retries: N` are re-sent, and
  only after a transient transport failure. Non-idempotent commands are never auto-retried (the loader rejects
  `retries > 0` without `idempotent: true`). Protocol-level backoff (HTTP 503, BLE "busy") is not automated — the
  caller decides. *Why:* "send once, observe the truth" is the default, not a test-mode flag.

- **Fire-and-forget is a manifest flag.** `expects_response: false` (per transport) means: send, swallow a
  `TransportTimeoutError` as success, skip `codec.decode`, return `None`, so the handler's `response` type goes unused
  (not currently enforced at build time). *Why:* replaces a hand-maintained allow-list with a flag the generator and
  runtime both see.

- **BLE: one transport per physical connection, named channels per command.** Channels are `(write, notify)` UUID
  pairs on the transport; commands name the channel they ride. End users never see UUIDs. *Why:* one transport per
  characteristic pair would force users to juggle several transport objects for one device.

---

## Results and classification

- **Every response is a `Result[T]` with a five-state `Classification`.** `ACCEPTED`, `REJECTED`, `DEVICE_FAULT`,
  `TRANSPORT_FAILURE`, `ANOMALOUS`. Names stay device-neutral.

- **Universal rules in the runtime, device rules in a `ResponseInterpreter`.** The runtime ships only universal
  classification (transport failure, anomalous, HTTP 5xx → fault, HTTP 4xx → reject). Application status enums, TLV
  "accepted" flags, etc. live in a user-supplied interpreter. Never add device-specific rules to `kandra_runtime`.

- **Interpreters are wired in the manifest: per transport, overridable per operation.** `interpreter: module:Class` on
  a transport classifies every reply on it; the same key in an operation's wiring block replaces it for that operation.
  Precedence: operation, then transport, then the family default (HTTP status codes; BLE / loopback always accepted).
  *Why:* a device that reports failure inside a `200` body or a BLE reply must be classifiable without editing
  generated code, and real devices are inconsistent from one endpoint to the next.

- **Fail-fast is opt-in.** Callers check `.accepted` / read `.data`. A test framework arms
  `client.on_non_accepted = ...` itself; `ignore_failures()` suppresses it for negative-path assertions.

---

## Device quirks

- **No hidden device-specific behavior in built-in transports.** A quirk gets a *name*, lives in the device's source
  tree, and is wired by the manifest — typically a per-command `codec:` override (e.g. a raw-bytes download alongside
  JSON endpoints on one HTTP transport). Header/envelope quirks ride in that same codec, since a codec produces the
  entire wire request; there is no separate request-filter concept.

- **The five-year-old-bug test.** If a future engineer asks "why does this command behave differently?", the answer
  must be one manifest grep plus one source-file open away. No `if "foo" in url` inside a shared client.

---

## Lifecycle and primitives

- **Discovery, enrollment, and identity are first-class.** "Enrollment" (not "pairing") covers BLE bonding,
  client certs, bearer tokens, and the no-op case. `IdentityStore` is pluggable with an XDG-JSON default. The generated
  `discover_and_connect()` serves the common case; the explicit `scan_*()` → `enroll()` → `connect()` flow stays
  available for custom auth and disambiguation.

- **Events are a third primitive**, distinct from attribute-subscribe.

- **Polling is never a default.** HTTP attribute/event subscribe uses SSE unless `mode: poll` is set, which requires
  an `interval`.

- **Stateful sessions** are a `session_required` command flag plus a per-transport `auth:` block (the handshake
  recipe is reserved, not yet wired).

---

## Generation, audiences, and vendoring

- **Import boundary.** `kandra-runtime` never imports `kandra.*` (`make check-boundary`). Generated SDKs depend only
  on the runtime and never reach back into the authoring tree.

- **`--profile` activates vendoring.** Without a profile, the SDK imports from `source_roots`, so plain builds are
  unaffected.

- **Vendoring re-homes, it doesn't flatten.** Each source root moves under `<pkg>/_internal/`, preserving relative
  paths. The rewrite is textual so comments — critically `# kandra-audience:` headers — survive verbatim.

- **Audience leaks fail loud.** A kept file importing a dropped file, or a surviving command whose own
  handler/model/codec was dropped, is a build error. Leakage matching is case-sensitive substring (mirrors `grep`).

---

## Logging and versioning

- **Stdlib `logging`, no handlers configured by the runtime.** `DEBUG` carries readable lifecycle events on
  `kandra.scan` / `kandra.ble` / `kandra.http`; the custom `NOISE` level (5) carries wire hexdumps on
  `kandra.wire.<command-id>`. *Why:* `DEBUG` must stay readable on real hardware.

- **Versioning is manual semver.** `_generated_from.json` records build provenance but never derives the version.
