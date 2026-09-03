"""Kandra runtime: protocols and helpers shipped to generated SDK clients."""

from kandra_runtime.ble import BleChannelCodec, BleRequest
from kandra_runtime.ble_scanner import BleScanner
from kandra_runtime.ble_transport import BleTransport
from kandra_runtime.codec import Codec
from kandra_runtime.command import Command, dispatch, dispatch_sync
from kandra_runtime.enrollment import (
    BleEnrollment,
    Enrollment,
    EnrollmentError,
    HttpEnrollment,
)
from kandra_runtime.errors import (
    CodecError,
    KandraError,
    TransportError,
    TransportNotOpenError,
    TransportTimeoutError,
)
from kandra_runtime.http import (
    DefaultHttpResponseInterpreter,
    HttpJsonCodec,
    HttpMethod,
    HttpRequest,
    HttpResponse,
    default_http_interpreter,
)
from kandra_runtime.http_scanner import HttpScanner
from kandra_runtime.http_transport import HttpTransport
from kandra_runtime.identity import (
    BleIdentity,
    CompositeIdentity,
    HttpIdentity,
    Identity,
    IdentityNotFoundError,
    IdentityStore,
    WifiCredentials,
)
from kandra_runtime.identity_store_file import PlatformDirsJsonStore
from kandra_runtime.loopback import LoopbackTransport
from kandra_runtime.result import (
    AlwaysAcceptedResponseInterpreter,
    Classification,
    ClassificationVerdict,
    ResponseInterpreter,
    Result,
    always_accepted_interpreter,
    format_failure,
)
from kandra_runtime.scanner import (
    Candidate,
    Matcher,
    Scanner,
    accept_all,
    snapshot_scan,
)
from kandra_runtime.transport import Transport, open_transport

__all__ = [
    "AlwaysAcceptedResponseInterpreter",
    "BleChannelCodec",
    "BleEnrollment",
    "BleIdentity",
    "BleRequest",
    "BleScanner",
    "BleTransport",
    "Candidate",
    "Classification",
    "ClassificationVerdict",
    "ResponseInterpreter",
    "Codec",
    "CodecError",
    "Command",
    "CompositeIdentity",
    "DefaultHttpResponseInterpreter",
    "Enrollment",
    "EnrollmentError",
    "HttpEnrollment",
    "HttpIdentity",
    "HttpJsonCodec",
    "HttpMethod",
    "HttpRequest",
    "HttpResponse",
    "HttpScanner",
    "HttpTransport",
    "Identity",
    "IdentityNotFoundError",
    "IdentityStore",
    "KandraError",
    "LoopbackTransport",
    "Matcher",
    "PlatformDirsJsonStore",
    "Result",
    "Scanner",
    "Transport",
    "TransportError",
    "TransportNotOpenError",
    "TransportTimeoutError",
    "WifiCredentials",
    "accept_all",
    "always_accepted_interpreter",
    "default_http_interpreter",
    "dispatch",
    "dispatch_sync",
    "format_failure",
    "open_transport",
    "snapshot_scan",
]
