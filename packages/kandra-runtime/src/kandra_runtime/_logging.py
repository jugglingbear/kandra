"""Logging conventions for the runtime.

The runtime logs on the ``kandra.*`` logger namespace using the standard library :mod:`logging` only — it never
configures handlers or levels itself, so applications stay in full control of output.

Two tiers of verbosity are used:

- ``DEBUG`` — high-level lifecycle events that are useful when watching a session: scan start/stop, each discovered
  candidate, HTTP requests and responses, BLE connect / write / notify.
- ``NOISE`` — a custom level *below* ``DEBUG`` for byte-level wire traces (the encoded request hexdump). Enable it
  only when you need to inspect raw payloads; ``DEBUG`` stays readable without it.

Logger names:

- ``kandra.scan`` — BLE/HTTP discovery.
- ``kandra.ble`` — BLE transport (connect, write, notify, disconnect).
- ``kandra.http`` — HTTP transport (request, response).
- ``kandra.wire.<command-id>`` — per-command wire hexdump, emitted at ``NOISE``.

Example:
    Show high-level activity but not raw bytes::

        logging.getLogger("kandra").setLevel(logging.DEBUG)

    Also show the wire hexdump for one command::

        import kandra_runtime

        logging.getLogger("kandra.wire.shutter.start").setLevel(kandra_runtime.NOISE)
"""

from __future__ import annotations

import logging

NOISE = 5
"""Custom level below ``DEBUG`` (10) for ultra-verbose byte-level wire traces."""

logging.addLevelName(NOISE, "NOISE")
