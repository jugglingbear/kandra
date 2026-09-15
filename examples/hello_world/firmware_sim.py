"""Tiny firmware simulator for the Hello World device.

One route -- ``PUT /hello`` -- always answers ``{"reply": "world"}``. Run it,
then run ``demo.py`` against it::

    poetry run python -m examples.hello_world.firmware_sim

Serves on ``http://localhost:8080`` (override the port with ``PORT``).
"""

from __future__ import annotations

import os

from flask import Flask, jsonify
from flask.wrappers import Response


def create_app() -> Flask:
    """Build the one-route app."""
    app = Flask(__name__)

    @app.put("/hello")
    def hello() -> Response:
        # The request body ("hello") is ignored; the device always says "world".
        return jsonify({"reply": "world"})

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    create_app().run(host="127.0.0.1", port=port)
