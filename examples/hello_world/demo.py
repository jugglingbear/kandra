"""Minimal end-to-end demo for the generated ``hello_world_sdk``.

Sends one HTTP ``PUT /hello`` and prints the device's reply. To run it:

1. Build the SDK::

       kandra build examples/hello_world/manifest.yaml     # writes examples/hello_world/dist/

2. Start the tiny sim in one terminal::

       poetry run python -m examples.hello_world.firmware_sim

3. Run this demo in another::

       PYTHONPATH="examples/hello_world/dist:examples/hello_world/src" \
           poetry run python examples/hello_world/demo.py

Point at real hardware instead of the sim by setting ``HELLO_WORLD_URL``.
"""

from __future__ import annotations

import asyncio
import os

from hello.greeter import HelloRequest
from hello_world_sdk import HelloWorldClient, TransportId
from kandra_runtime import HttpTransport, open_transport

BASE_URL = os.environ.get("HELLO_WORLD_URL", "http://localhost:8080")


async def main() -> None:
    """Open a transport, build the client, send one command."""
    # No discovery/enrollment here -- point a transport straight at the URL and
    # hand it to the client. (The pneumatic_bear_poker example shows the full
    # discover -> enroll -> save -> connect lifecycle.)
    transport = HttpTransport(BASE_URL)
    async with open_transport(transport):
        client = HelloWorldClient(transports={TransportId.HTTP: transport})
        result = await client.greeting.hello(HelloRequest(message="hello"))

    assert result is not None and result.data is not None
    print(f"PUT hello -> {result.data.reply}")


if __name__ == "__main__":
    asyncio.run(main())
