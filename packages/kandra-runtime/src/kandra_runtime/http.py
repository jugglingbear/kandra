"""HTTP transport-family envelope types, codec base, and default interpreter.

See kandra.md sections 11.1 (envelope shape), 11.5 (Result[T] /
classification).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Generic, Literal, get_type_hints

from kandra_runtime.codec import RequestT, ResponseT
from kandra_runtime.errors import CodecError
from kandra_runtime.result import Classification, ClassificationVerdict

HttpMethod = Literal["GET", "POST", "PUT", "DELETE"]


@dataclass(frozen=True)
class HttpRequest:
    """Wire envelope consumed by the HTTP transport's ``request()``.

    The codec produces this from the user's typed request dataclass;
    the transport executes it.
    """

    method: HttpMethod
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class HttpResponse:
    """Wire envelope produced by the HTTP transport's ``request()``.

    The codec parses the body into the user's typed response dataclass.
    Classification of the HTTP status (5xx -> DEVICE_FAULT, 4xx ->
    REJECTED, etc.) is the result-envelope's job, not the codec's.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class HttpJsonCodec(Generic[RequestT, ResponseT]):
    """JSON body codec for HTTP commands with dataclass-shaped payloads.

    Fixes ``WireReqT`` / ``WireRespT`` to ``HttpRequest`` /
    ``HttpResponse`` so users only ever write two type parameters --
    see kandra.md section 11.5 (Q1 decision).

    The codec is constructed with the wire-format metadata that the
    *generator* reads off the manifest's ``http:`` block, plus the
    request/response types it reads off the handler class. End users
    do not instantiate this directly.
    """

    def __init__(
        self,
        *,
        method: HttpMethod,
        path: str,
        request_type: type[RequestT],
        response_type: type[ResponseT] | None,
        query_from_request: bool = False,
    ) -> None:
        """Build a codec bound to a single HTTP endpoint."""
        self._method = method
        self._path = path
        self._request_type = request_type
        self._response_type = response_type
        self._query_from_request = query_from_request

    def encode(self, request: RequestT) -> HttpRequest:
        """Serialize ``request`` into an :class:`HttpRequest` envelope."""
        if not is_dataclass(request) or isinstance(request, type):
            raise CodecError(
                f"HttpJsonCodec requires a dataclass instance, got {type(request).__name__}"
            )
        fields = asdict(request)
        if self._query_from_request:
            # GET-style: request fields become query params, no body.
            query = {k: _stringify(v) for k, v in fields.items() if v is not None}
            return HttpRequest(method=self._method, path=self._path, query=query)
        body = json.dumps(fields, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return HttpRequest(method=self._method, path=self._path, headers=headers, body=body)

    def decode(self, response: HttpResponse) -> ResponseT:
        """Deserialize ``response`` body into the declared response type."""
        if self._response_type is None:
            raise CodecError(f"command at {self._path!r} declared no response type")
        if not response.body:
            raise CodecError(f"empty response body from {self._path!r}")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodecError(f"invalid JSON from {self._path!r}: {exc}") from exc
        try:
            hints = get_type_hints(self._response_type)
            kwargs: dict[str, Any] = {name: payload[name] for name in hints if name in payload}
            return self._response_type(**kwargs)
        except (TypeError, KeyError) as exc:
            raise CodecError(
                f"cannot build {self._response_type.__name__} from {self._path!r} response: {exc}"
            ) from exc


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class DefaultHttpResponseInterpreter:
    """Stock HTTP interpreter keyed off response status code.

    Rules:

    - ``2xx`` → :attr:`Classification.ACCEPTED`
    - ``4xx`` → :attr:`Classification.REJECTED`
    - ``5xx`` → :attr:`Classification.DEVICE_FAULT`
    - everything else (1xx, 3xx, non-standard) → :attr:`Classification.ANOMALOUS`

    Device-specific rules (e.g. parsing a JSON error envelope to surface
    the device's own error code) live in user-supplied interpreters; this
    one is the runtime default for any HTTP transport that doesn't
    declare its own.
    """

    def classify(self, response: HttpResponse) -> ClassificationVerdict:
        """Map the HTTP status to a :class:`ClassificationVerdict`."""
        status = response.status
        extra = {"http_status": status}
        if 200 <= status < 300:
            return ClassificationVerdict(Classification.ACCEPTED, extra=extra)
        if 400 <= status < 500:
            return ClassificationVerdict(
                Classification.REJECTED, reason=f"HTTP {status}", extra=extra
            )
        if 500 <= status < 600:
            return ClassificationVerdict(
                Classification.DEVICE_FAULT, reason=f"HTTP {status}", extra=extra
            )
        return ClassificationVerdict(
            Classification.ANOMALOUS, reason=f"unexpected HTTP {status}", extra=extra
        )


default_http_interpreter = DefaultHttpResponseInterpreter()
"""Module-level singleton; safe to share across commands (no state)."""
