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

For BLE — and for HTTP devices with vendor-specific error envelopes (e.g. `{"status": "ok", "result_generic": -42}`) —
you plug in a custom `ResponseInterpreter`. Manifest entries can declare a interpreter per transport family or per
command.

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

The model is Kandra's own result envelope — see `kandra.md` §8 and §11.5 for the design history.
