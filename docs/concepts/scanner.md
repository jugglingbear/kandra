# Scanner

A **Scanner** discovers in-range devices and surfaces them as [`Candidate`](#the-candidate-record) records. It is
deliberately decoupled from [Enrollment](enrollment.md) — a candidate is a *possibility*, not yet a paired device.

## The Candidate Record

```{eval-rst}
.. autoclass:: kandra_runtime.scanner.Candidate
   :no-index:
```

Transport-specific facts (BLE service UUIDs, mDNS TXT records, HTTP `Server:` header, RSSI, …) live in `metadata` as a
free-form mapping — the [Matcher](#matcher) is where you read them.

## The Scanner Protocol

```{eval-rst}
.. autoclass:: kandra_runtime.scanner.Scanner
   :members:
   :no-index:
```

Two entry points are exposed for two different UX shapes:

- **`Scanner.scan(...)`** — streaming `AsyncIterator[Candidate]`. Yields
  each match as soon as it's observed. Best for "connect to the first
  one" flows and live UIs that render devices as they appear.
- **`snapshot_scan(...)`** — collect-for-N-seconds helper that consumes
  `scan()` and returns a deduplicated list. Best for "show me the
  menu, let me pick" CLI flows.

## Matcher

`Matcher` is a type alias for `Callable[[Candidate], bool]` — any predicate that takes a candidate and returns whether
to surface it.

```{eval-rst}
.. autofunction:: kandra_runtime.scanner.accept_all
   :no-index:
```

The manifest's `discovery.match` block is compiled into a `Matcher` at build time, so generated `scan_http()` /
`scan_ble()` helpers ship with the correct predicate baked in. You only write a custom `Matcher` for ad-hoc tooling.

## Overriding the Probe URLs (HTTP)

The generated `scan_http()` helper bakes in the manifest's `discovery.http.base_urls`. That's the right behavior in
production, but it's hostile for local development against a simulator on `http://localhost:PORT/`. Both
`scan_http()` and `make_http_scanner()` accept an optional `base_urls=` keyword that replaces the manifest list:

```python
# Production: probe the manifest URLs.
candidates = await scan_http(timeout=5.0)

# Local sandbox: probe a Docker sim on loopback instead.
candidates = await scan_http(timeout=2.0, base_urls=["http://localhost:8080"])
```

The probe path and matcher are unchanged — only the candidate URL list is overridden.

## How Discovery Unfolds (HTTP Example)

```{mermaid}
sequenceDiagram
    autonumber
    participant App as Your app
    participant Scanner as HttpScanner
    participant DevA as Device A<br/>(your kind)
    participant DevB as Device B<br/>(wrong kind)
    participant DevC as Device C<br/>(unreachable)

    App->>Scanner: snapshot_scan(timeout=5)
    par Parallel probes
        Scanner->>DevA: GET /.well-known/<id>
        Scanner->>DevB: GET /.well-known/<id>
        Scanner->>DevC: GET /.well-known/<id>
    end
    DevA-->>Scanner: 200 OK + identifying body
    DevB-->>Scanner: 404 Not Found
    Note over Scanner,DevC: Times out silently

    Scanner->>Scanner: matcher(DevA) -> True
    Scanner->>Scanner: matcher(DevB) -> False (no body)
    Scanner-->>App: [Candidate(DevA)]
```

The HTTP scanner does its probing in parallel; the BLE scanner subscribes to OS-level advertisement callbacks. Either
way, the user-facing API is the same `AsyncIterator[Candidate]` / `snapshot_scan(...)` pair.

## When to Write Your Own Scanner

Almost never. The built-in `BleScanner` and `HttpScanner` cover the two transports the runtime ships with; if you add a
new transport family (Zigbee, USB, gRPC discovery), you'd implement `Scanner` for that family and register it with the
generator. The `@runtime_checkable` `Protocol` shape means you don't need to subclass anything — just satisfy the
contract.
