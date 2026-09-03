# Architecture

Kandra is split into two cleanly separated halves: a **build-time generator** that consumes a manifest and emits a typed
Python package, and a **runtime library** the generated package imports at execution time. The runtime is a fixed
library shipped by Kandra — users import from it and implement its Protocols in their own code, but they don't fork or
modify it. Adding a brand-new transport family is the only scenario that touches `kandra_runtime` itself, and that's an
upstream contribution rather than a per-project extension.

## The Two Halves

```{mermaid}
flowchart TB
    subgraph buildtime["Build time: `kandra build`"]
        manifest["manifest.yaml<br/>(transports, commands,<br/>discovery, codecs)"]
        handlers["Request / Response<br/>dataclasses<br/>(user-authored)"]
        loader["kandra.loader"]
        validator["kandra.manifest<br/>(Pydantic validation)"]
        generator["kandra.generator"]
        manifest --> loader --> validator --> generator
        handlers --> generator
    end

    subgraph artifact["Generated artifact"]
        client["MyDeviceClient"]
        scanners["scan_ble / scan_http"]
        transports["_TRANSPORT_FACTORIES"]
        registry["Command registry"]
    end

    subgraph runtime["Runtime: `kandra_runtime`"]
        rt_identity["Identity / IdentityStore"]
        rt_scanner["Scanner / Candidate"]
        rt_enroll["Enrollment"]
        rt_transport["Transport (BLE / HTTP)"]
        rt_codec["Codec"]
        rt_result["Result / ResponseInterpreter"]
    end

    usercode["Your app code<br/>(`discover_and_connect`<br/>or custom enroll script)"]

    generator --> client
    generator --> scanners
    generator --> transports
    generator --> registry

    client -. imports .-> rt_identity
    client -. imports .-> rt_transport
    client -. imports .-> rt_enroll
    scanners -. imports .-> rt_scanner
    registry -. imports .-> rt_codec
    registry -. imports .-> rt_result

    usercode -. uses .-> client
    usercode -. imports .-> rt_enroll
```

The split exists so the **runtime never sees your manifest** — generated SDKs ship with only the runtime as a
dependency, which makes them small, auditable, and easy to vendor into closed-source distributions.

For the common case, the generated `MyDeviceClient.discover_and_connect()` classmethod drives the whole lifecycle (scan
→ enroll → save → connect) on first run, and short-circuits to a plain `connect()` thereafter — so your app code only
ever imports the generated client plus an `Enrollment` adapter. The explicit two-step "setup script then app script"
split is still available for custom auth flows and bench scenarios where the first-match-wins heuristic isn't right; see
[Lifecycle](lifecycle.md) for both patterns.

## Six Core Abstractions

The runtime defines six interlocking protocols. Each gets a concept page; this is the executive summary.

```{list-table}
:header-rows: 1
:widths: 20 30 50

* - Concept
  - One-liner
  - You typically...
* - [Scanner](scanner.md)
  - Discovers in-range candidates.
  - Pick one of the built-in adapters; manifest defines match rules.
* - [Enrollment](enrollment.md)
  - One-time handshake that produces a persistent identity.
  - Use defaults; subclass for custom auth flows.
* - [Identity](identity.md)
  - "What I need to reconnect to a saved device."
  - Don't write your own — runtime supplies BLE / HTTP / Composite.
* - [Transport](transport.md)
  - Carries one request envelope to the device and one response back.
  - Pick a built-in (BLE / HTTP); rarely write your own.
* - [Codec](codec.md)
  - Encodes a request for the wire; decodes the reply into a typed response.
  - Subclass the built-in for your transport family (e.g. `HttpJsonCodec`); rarely write from scratch.
* - [Result](result.md)
  - Typed envelope around every command's outcome (response + verdict).
  - Inspect `.accepted` / `.data` / `.classification`.
```

## How They Fit Together

```{mermaid}
classDiagram
    direction LR

    class Scanner {
        <<Protocol>>
        +scan(matcher: Matcher, timeout: float) AsyncIterator~Candidate~
    }
    class Candidate {
        +transport: str
        +address: str
        +advertised_name: str?
        +metadata: Mapping
    }
    class Enrollment {
        <<Protocol>>
        +enroll(candidate: Candidate, saved_name: str) Identity
    }
    class Identity {
        <<discriminated union>>
        +saved_name: str
        +transport: Literal
    }
    class IdentityStore {
        <<Protocol>>
        +save(identity: Identity)
        +load(saved_name: str) Identity
        +list_saved() list~str~
    }
    class Transport {
        <<Protocol>>
        +open() / close()
        +request(envelope: WireReqT) WireRespT
        +from_identity(identity: Identity)$ Transport
    }
    class Codec {
        <<Protocol>>
        +encode(request: RequestT) WireReqT
        +decode(wire_response: WireRespT) ResponseT
    }
    class ResponseInterpreter {
        <<Protocol>>
        +classify(wire_response: WireRespT) Verdict
    }
    class Result~T~ {
        +classification: Classification
        +data: T?
        +accepted: bool
    }
    class Command~Req,Resp~ {
        +codec: Codec
        +transport: Transport
        +interpreter: ResponseInterpreter
        +dispatch(request: RequestT) Result~ResponseT~
    }

    Scanner ..> Candidate : yields
    Enrollment ..> Candidate : consumes
    Enrollment ..> Identity : produces
    IdentityStore ..> Identity : stores
    Transport ..> Identity : built from
    Command --> Codec : holds
    Command --> Transport : holds
    Command --> ResponseInterpreter : holds
    Codec ..> Transport : shares wire types
    ResponseInterpreter ..> Result : produces
    Codec ..> Result : decodes into
```

Codec and Transport never reference each other directly — `Command` holds both and pipes the
wire envelope between them. The dotted "shares wire types" edge is a *type-level* constraint
(`Codec[..., WireReqT, WireRespT]` must match `Transport[WireReqT, WireRespT]`), enforced by
the generics at construction time.

## Data Flow at Runtime

The end-to-end happy path for a single command dispatch, against an already-enrolled device:

```{mermaid}
sequenceDiagram
    autonumber
    participant App as Your app
    participant Client as MyDeviceClient
    participant Store as IdentityStore
    participant Transport as HttpTransport
    participant Codec as HttpJsonCodec
    participant ResponseInterpreter as DefaultHttpResponseInterpreter
    participant Device as Real device

    App->>Client: connect("my-saved-device")
    Client->>Store: load("my-saved-device")
    Store-->>Client: HttpIdentity
    Client->>Transport: from_identity(identity)
    Transport->>Device: open() (TCP / TLS handshake)

    App->>Client: bear_poker.deploy(DeployRequest(...))
    Client->>Codec: encode(request)
    Codec-->>Client: HttpRequest envelope
    Client->>Transport: request(envelope)
    Transport->>Device: HTTP POST /v1/poker/deploy
    Device-->>Transport: HTTP 200 + body
    Transport-->>Client: HttpResponse envelope
    Client->>ResponseInterpreter: classify(response)
    ResponseInterpreter-->>Client: Verdict(ACCEPTED)
    Client->>Codec: decode(response)
    Codec-->>Client: DeployResponse(...)
    Client-->>App: Result[DeployResponse](accepted=True, data=...)
```

See [Lifecycle](lifecycle.md) for the broader "first time meeting the device" walkthrough (discover → enroll → save →
connect → dispatch).
