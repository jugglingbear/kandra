# Result

Every command dispatched through a generated client returns a **`Result[T]`** — a typed envelope around the response
data and a five-state **classification** describing what actually happened.

## The Five Classifications

```{eval-rst}
.. autoclass:: kandra_runtime.result.Classification
   :members:
   :no-index:
```

```{list-table}
:header-rows: 1

* - State
  - Meaning
  - `.data`?
* - `ACCEPTED`
  - Device took the command and codec parsed the response
  - Populated
* - `REJECTED`
  - Device received and refused (HTTP 4xx, protocol error code)
  - `None`
* - `DEVICE_FAULT`
  - Device tried and its firmware faulted (HTTP 5xx)
  - `None`
* - `TRANSPORT_FAILURE`
  - Round-trip never completed (timeout, connection refused)
  - `None`
* - `ANOMALOUS`
  - Response violated SDK expectations (200 OK but unparseable)
  - `None`
```

The states are mutually exclusive and exhaustive; every wire response classifies into exactly one of them.

## The Envelope

```{eval-rst}
.. autoclass:: kandra_runtime.result.Result
   :members:
   :no-index:
```

The convenience properties (`accepted` / `ok`, `failed`, `rejected`, `device_faulted`, `transport_failed`,
`anomalous`) let you branch without importing the `Classification` enum:

```python
result = await client.poker.deploy(req)
if result.ok:
    print(result.data.delivered_psi)
elif result.transport_failed:
    print("retry later:", result.reason)
else:
    print("device says no:", result.classification, result.extra)
```

`ok` is an alias for `accepted` (a short, terminal-friendly name for sandbox prints), and `failed` is its inverse —
True for every non-ACCEPTED classification.

## ResponseInterpreter Pipeline

```{mermaid}
flowchart LR
    wire[Wire response<br/>e.g. HttpResponse] --> interpreter[ResponseInterpreter.classify]
    interpreter --> verdict[ClassificationVerdict<br/>classification + reason + extra]
    verdict -->|accepted| codec[Codec.decode]
    verdict -->|not accepted| skipdecoder[(skip decode)]
    codec --> data[ResponseT]
    data --> result[Result&lt;T&gt;]
    skipdecoder --> result
```

The dispatcher always runs the interpreter *first*. Decoding only happens on `ACCEPTED` — saving wasted work on rejected
responses and ensuring `.data is None` for every non-ACCEPTED outcome.

## ResponseInterpreter Protocol

```{eval-rst}
.. autoclass:: kandra_runtime.result.ResponseInterpreter
   :members:
   :no-index:
.. autoclass:: kandra_runtime.result.ClassificationVerdict
   :no-index:
```

The runtime ships a sensible default for HTTP (`default_http_interpreter`) keyed off status codes:

```{list-table}
:header-rows: 1

* - HTTP status
  - Classification
* - 2xx
  - ACCEPTED
* - 4xx
  - REJECTED
* - 5xx
  - DEVICE_FAULT
* - Connection error / timeout
  - TRANSPORT_FAILURE
* - 2xx but body parse fails
  - ANOMALOUS
```

Unless the manifest says otherwise, the generator wires the family default: `default_http_interpreter` for HTTP (the
status-code rules above), and `always_accepted_interpreter` for BLE / loopback — there the codec itself flags a bad
response by raising `CodecError` (which surfaces as `ANOMALOUS`).

### Custom Interpreters

When a device reports failure some other way — an HTTP device that answers `200` with `{"ok": false, "error": "busy"}`,
or a BLE reply carrying a status byte — write a class that implements the protocol above and name it on the transport:

```yaml
transports:
  - id: wifi
    family: http
    interpreter: mydevice.interpreters:EnvelopeInterpreter
```

```python
import json

from kandra_runtime import Classification, ClassificationVerdict, HttpResponse


class EnvelopeInterpreter:
    """Read the device's own ok/error envelope instead of trusting the HTTP status alone."""

    def classify(self, response: HttpResponse) -> ClassificationVerdict:
        if response.status >= 500:
            return ClassificationVerdict(Classification.DEVICE_FAULT, reason=f"HTTP {response.status}")
        body = json.loads(response.body or b"{}")
        if response.status >= 400 or body.get("ok") is False:
            return ClassificationVerdict(Classification.REJECTED, reason=body.get("error", f"HTTP {response.status}"))
        return ClassificationVerdict(Classification.ACCEPTED)
```

The class is constructed with no arguments and classifies every reply on that transport — commands, attribute
operations, and events alike. Like a handler or codec it lives under a `source_roots` path, so a `--profile` build
vendors it with the rest of the SDK.

#### When One Operation Reports Errors Differently

Real devices are often inconsistent: most endpoints use HTTP status codes, but one older endpoint answers `200` with
the error in the body. Name an interpreter in that operation's wiring block and it replaces the transport's for that
operation only:

```yaml
commands:
  - id: poker.calibrate
    handler: mydevice.handlers.poker:Calibrate
    transports: [wifi]
    audience: [internal]
    http:
      wifi:
        method: POST
        path: /legacy/calibrate
        interpreter: mydevice.interpreters:EnvelopeInterpreter
```

Every wiring block accepts `interpreter:`: a command's `http:` and `ble:` blocks, an attribute's `read`, `write`, and
`subscribe` blocks (or its `ble:` block), and an event's `http:` and `ble:` blocks. For each reply the SDK uses the
first of:

1. the operation's own `interpreter:`,
2. the transport's `interpreter:`,
3. the family default (status codes for HTTP; every reply accepted for BLE and loopback).

## Why an Envelope, Not Exceptions?

Three reasons:

1. **Tests.** Most automated tests dispatch a command, then assert on the
   outcome. With an envelope, that's `assert result.accepted` — no
   `pytest.raises` gymnastics, no swallowing of unrelated exceptions.
2. **Diagnostics.** `result.classification` is a structured value you
   can log, count, and aggregate. An exception's `str()` is harder.
3. **Five-way branch.** "Rejected" and "device fault" and "transport
   failed" deserve different recovery logic. A single exception
   hierarchy forces awkward `isinstance` chains.
