"""Built-in BLE transport backed by ``bleak``.

One :class:`BleTransport` owns one ``BleakClient`` for one physical
device connection. Channels are declared on the transport at
construction time: each channel is a (write_uuid, notify_uuid) pair
that commands address by name (see :class:`~kandra_runtime.ble.BleRequest`).

**Framing:** one write produces one notification packet.
Multi-packet fragmentation / reassembly is the responsibility of the
user's payload codec, *or* of a future framing wrapper layer. Channels
are not concurrent-safe at the transport level; each channel has an
internal lock so two ``request()`` calls on the same channel will
serialize, but interleaved use is still considered the caller's bug.

See kandra.md section 11.9.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from kandra_runtime.ble import BleRequest
from kandra_runtime.errors import TransportError, TransportNotOpenError, TransportTimeoutError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


# ---------------------------------------------------------------------------
# Minimal BleakClient-shaped protocol so tests can inject a fake without
# depending on real BLE hardware. Real bleak.BleakClient satisfies this
# structurally.
# ---------------------------------------------------------------------------


@runtime_checkable
class _BleakLike(Protocol):
    """Subset of ``bleak.BleakClient`` that :class:`BleTransport` calls."""

    @property
    def is_connected(self) -> bool:
        """True after a successful ``connect()`` and before ``disconnect()``."""
        ...

    async def connect(self) -> None:
        """Establish the GATT connection."""
        ...

    async def disconnect(self) -> None:
        """Tear down the GATT connection."""
        ...

    async def start_notify(
        self,
        char_specifier: Any,
        callback: Any,
    ) -> None:
        """Subscribe to notifications from a characteristic."""
        ...

    async def stop_notify(self, char_specifier: Any) -> None:
        """Unsubscribe from notifications."""
        ...

    async def write_gatt_char(
        self,
        char_specifier: Any,
        data: bytes,
        response: bool = False,
    ) -> None:
        """Write to a characteristic."""
        ...


_ClientFactory = "Callable[[str], _BleakLike]"


class BleTransport:
    """Async BLE transport keyed on named (write_uuid, notify_uuid) channels.

    Args:
        address: BLE peripheral address (MAC on Linux/Windows; UUID on macOS).
        channels: Mapping of channel name to ``(write_uuid, notify_uuid)``.
            Channel names match the per-command ``ble.channel:`` field
            in the manifest.
        client_factory: Optional callable that builds the underlying
            BLE client from the address. Defaults to ``bleak.BleakClient``.
            Inject a fake here for unit tests.
        connect_timeout: Seconds to wait for ``connect()`` before giving up.
    """

    def __init__(
        self,
        address: str,
        *,
        channels: Mapping[str, tuple[str, str]],
        client_factory: Callable[[str], _BleakLike] | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        """Initialize transport configuration. No I/O occurs here."""
        if not address:
            raise ValueError("BleTransport address must be non-empty")
        if not channels:
            raise ValueError("BleTransport requires at least one channel")
        for name, pair in channels.items():
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError(
                    f"channel {name!r}: value must be (write_uuid, notify_uuid) tuple"
                )
            write_uuid, notify_uuid = pair
            if not write_uuid or not notify_uuid:
                raise ValueError(f"channel {name!r}: both UUIDs must be non-empty")
        self._address = address
        self._channels: dict[str, tuple[str, str]] = dict(channels)
        # Reverse index: notify_uuid -> channel name (so the notify callback
        # can route incoming bytes to the right queue).
        self._notify_to_channel: dict[str, str] = {
            notify_uuid: name for name, (_, notify_uuid) in self._channels.items()
        }
        self._connect_timeout = connect_timeout
        self._client_factory: Callable[[str], _BleakLike] = (
            client_factory if client_factory is not None else _default_client_factory
        )
        self._client: _BleakLike | None = None
        self._queues: dict[str, asyncio.Queue[bytes]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def from_identity(
        cls,
        identity: Any,
        *,
        channels: Mapping[str, tuple[str, str]],
        client_factory: Callable[[str], _BleakLike] | None = None,
        connect_timeout: float = 10.0,
    ) -> BleTransport:
        """Build a transport from a persisted :class:`~kandra_runtime.identity.BleIdentity`.

        Equivalent to ``BleTransport(identity.address, channels=...)``
        — broken out as a classmethod so generated clients can stay
        identity-agnostic.
        """
        from kandra_runtime.identity import BleIdentity

        if not isinstance(identity, BleIdentity):
            raise TypeError(
                f"BleTransport.from_identity expected BleIdentity, "
                f"got {type(identity).__name__}"
            )
        return cls(
            identity.address,
            channels=channels,
            client_factory=client_factory,
            connect_timeout=connect_timeout,
        )

    async def open(self) -> None:
        """Connect and subscribe to notifications on every channel."""
        if self._client is not None and self._client.is_connected:
            return
        client = self._client_factory(self._address)
        try:
            await asyncio.wait_for(client.connect(), self._connect_timeout)
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"BLE connect to {self._address!r} timed out after {self._connect_timeout}s"
            ) from exc
        except Exception as exc:  # normalize bleak's exception zoo
            raise TransportError(f"BLE connect to {self._address!r} failed: {exc}") from exc
        # Pre-create queues + locks; subscribe to every notify uuid.
        for name, (_write_uuid, notify_uuid) in self._channels.items():
            self._queues[name] = asyncio.Queue()
            self._locks[name] = asyncio.Lock()
            try:
                await client.start_notify(notify_uuid, self._make_notify_callback(name))
            except Exception as exc:
                # Roll back: stop any notifies we already started, then disconnect.
                await self._cleanup_after_failed_open(client, started=name)
                raise TransportError(
                    f"BLE start_notify({notify_uuid!r}) on channel {name!r} failed: {exc}"
                ) from exc
        self._client = client

    async def close(self) -> None:
        """Unsubscribe from notifications and disconnect."""
        if self._client is None:
            return
        client = self._client
        # Best-effort stop_notify; don't let one bad characteristic block disconnect.
        for name, (_write_uuid, notify_uuid) in self._channels.items():
            with contextlib.suppress(Exception):
                await client.stop_notify(notify_uuid)
            self._queues.pop(name, None)
            self._locks.pop(name, None)
        try:
            await client.disconnect()
        finally:
            self._client = None

    @property
    def is_open(self) -> bool:
        """True once :meth:`open` succeeded and before :meth:`close`."""
        return self._client is not None and self._client.is_connected

    async def request(self, envelope: BleRequest) -> bytes:
        """Write ``envelope.payload`` to the channel and await one notification.

        Raises:
            TransportNotOpenError: if called before :meth:`open` (or after close).
            TransportError: on unknown channel name or bleak failure.
        """
        if self._client is None or not self._client.is_connected:
            raise TransportNotOpenError("BleTransport.request() called before open()")
        pair = self._channels.get(envelope.channel)
        if pair is None:
            raise TransportError(
                f"BLE channel {envelope.channel!r} not declared on transport "
                f"(known: {sorted(self._channels)!r})"
            )
        write_uuid, _notify_uuid = pair
        queue = self._queues[envelope.channel]
        lock = self._locks[envelope.channel]
        # Serialize per-channel; concurrent requests on one channel would
        # interleave responses ambiguously.
        async with lock:
            # Drain any stray notifications that arrived between requests.
            while not queue.empty():
                queue.get_nowait()
            try:
                await self._client.write_gatt_char(write_uuid, envelope.payload, response=False)
            except Exception as exc:
                raise TransportError(
                    f"BLE write_gatt_char({write_uuid!r}) failed: {exc}"
                ) from exc
            # Per-call timeout is enforced by the dispatcher (Command.timeout);
            # here we just block until the notification arrives or the task
            # is cancelled.
            return await queue.get()

    # -- internals --------------------------------------------------------

    def _make_notify_callback(
        self, channel_name: str
    ) -> Callable[[int, bytearray], None]:
        queue = self._queues[channel_name]

        def _on_notify(_sender: int, data: bytearray) -> None:
            queue.put_nowait(bytes(data))

        return _on_notify

    async def _cleanup_after_failed_open(
        self, client: _BleakLike, *, started: str
    ) -> None:
        for name, (_write_uuid, notify_uuid) in self._channels.items():
            if name == started:
                break
            with contextlib.suppress(Exception):
                await client.stop_notify(notify_uuid)
        self._queues.clear()
        self._locks.clear()
        with contextlib.suppress(Exception):
            await client.disconnect()


def _default_client_factory(address: str) -> _BleakLike:
    """Lazy import of bleak so the runtime module imports cheaply."""
    from bleak import BleakClient

    # bleak's signatures differ from the minimal _BleakLike protocol
    # in irrelevant ways (extra **kwargs, broader char-specifier types);
    # the runtime only ever calls them with str char specifiers and
    # positional args, so the cast is safe.
    return cast(_BleakLike, BleakClient(address))
