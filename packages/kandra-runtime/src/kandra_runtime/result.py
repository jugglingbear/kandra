"""Typed result envelope + classification pipeline.

A device-agnostic result model: a ``Result[T]`` envelope plus a five-state
``Classification`` (``DEVICE_FAULT`` etc.) carrying no device-specific
fields. Per-protocol classification rules (e.g. a vendor's status enum, BLE
TLV error codes) live in user code via the pluggable
:class:`ResponseInterpreter` protocol — the runtime ships only the generic
five-state taxonomy and a default HTTP interpreter keyed off status codes.

See kandra.md section 8 and section 11.5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping


_T = TypeVar("_T")
_WireRespT_contra = TypeVar("_WireRespT_contra", contravariant=True)


class Classification(Enum):
    """The five mutually exclusive outcomes of dispatching a command.

    The names are deliberately device-agnostic (``DEVICE_FAULT``, not
    ``CAMERA_FAULT``).
    """

    #: The device accepted the command and the codec parsed the response.
    ACCEPTED = auto()

    #: The device received the command and refused it (HTTP 4xx, protocol-level
    #: error code, etc).
    REJECTED = auto()

    #: The device tried to apply the command and its own firmware faulted
    #: (HTTP 5xx, internal-error code). Distinguished from REJECTED so callers
    #: can differentiate "you asked for something wrong" from "the device broke".
    DEVICE_FAULT = auto()

    #: The round-trip itself failed; no usable bytes arrived (connection
    #: refused, timeout, broken link).
    TRANSPORT_FAILURE = auto()

    #: The response violated SDK protocol expectations (200 OK with unparseable
    #: body, status/body disagreement, mandatory field missing).
    ANOMALOUS = auto()


@dataclass(frozen=True)
class Result(Generic[_T]):
    """The unified, typed result of dispatching a command.

    :attr:`data` is ``None`` for any non-ACCEPTED classification (or for
    void commands where the response carries no payload). Tests should
    branch on :attr:`classification` (or the boolean helpers) before
    consuming :attr:`data`.

    :attr:`extra` is an open-ended dict for device- or protocol-specific
    facts a custom :class:`ResponseInterpreter` wants to surface (e.g. a vendor's
    ``result_generic`` enum value, a BLE TLV opcode, an HTTP status
    code). The runtime never reads from it; it's purely for caller
    diagnostics and for :func:`format_failure`.
    """

    classification: Classification
    data: _T | None = None
    reason: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """``True`` when classification is :attr:`Classification.ACCEPTED`."""
        return self.classification is Classification.ACCEPTED

    @property
    def ok(self) -> bool:
        """Alias for :attr:`accepted` — convenient in sandbox/REPL prints."""
        return self.classification is Classification.ACCEPTED

    @property
    def failed(self) -> bool:
        """``True`` when classification is anything other than ACCEPTED."""
        return self.classification is not Classification.ACCEPTED

    @property
    def rejected(self) -> bool:
        """``True`` when classification is :attr:`Classification.REJECTED`."""
        return self.classification is Classification.REJECTED

    @property
    def device_faulted(self) -> bool:
        """``True`` when classification is :attr:`Classification.DEVICE_FAULT`."""
        return self.classification is Classification.DEVICE_FAULT

    @property
    def transport_failed(self) -> bool:
        """``True`` when classification is :attr:`Classification.TRANSPORT_FAILURE`."""
        return self.classification is Classification.TRANSPORT_FAILURE

    @property
    def anomalous(self) -> bool:
        """``True`` when classification is :attr:`Classification.ANOMALOUS`."""
        return self.classification is Classification.ANOMALOUS


# ---------------------------------------------------------------------------
# ResponseInterpreter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationVerdict:
    """A interpreter's verdict on a wire response, sans decoded payload.

    Returned by :class:`ResponseInterpreter.classify`. The dispatcher pairs the
    verdict with the codec's decoded data (or ``None`` when not ACCEPTED)
    to build the final :class:`Result`.
    """

    classification: Classification
    reason: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


class ResponseInterpreter(Protocol[_WireRespT_contra]):
    """Pluggable rule that turns a wire response into a :class:`ClassificationVerdict`.

    Implementations may live in user code (for protocol-specific rules
    like a vendor's ``result_generic`` enum or BLE TLV opcodes) or be
    imported from the runtime (e.g. :func:`default_http_interpreter`).
    """

    def classify(self, response: _WireRespT_contra) -> ClassificationVerdict:
        """Inspect ``response`` and return how it should be classified."""
        ...


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_failure(result: Result[Any]) -> str:
    """Build a single-line human summary of a non-ACCEPTED :class:`Result`.

    Returned by the framework-installed ``on_non_accepted`` hook before
    invoking the test-failure callback, and useful in log output for
    negative-path tests. Returns an empty string for ACCEPTED results.
    """
    if result.accepted:
        return ""
    parts: list[str] = [result.classification.name]
    if result.reason:
        parts.append(result.reason)
    for key, value in result.extra.items():
        parts.append(f"{key}={value!r}")
    return " | ".join(parts)


class AlwaysAcceptedResponseInterpreter:
    """Trivial interpreter: every response is :attr:`Classification.ACCEPTED`.

    Used as the default for transport families that don't have a built-in
    interpreter (loopback, user-supplied custom transports). Real
    transports should ship a status-aware interpreter.
    """

    def classify(self, response: Any) -> ClassificationVerdict:
        """Always return an ACCEPTED verdict regardless of ``response``."""
        return ClassificationVerdict(Classification.ACCEPTED)


always_accepted_interpreter = AlwaysAcceptedResponseInterpreter()
"""Module-level singleton; safe to share across commands (no state)."""
