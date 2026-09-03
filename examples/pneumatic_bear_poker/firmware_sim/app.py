"""Pneumatic Bear Poker firmware simulator.

A Flask app that implements every HTTP command in the example manifest
plus discovery (`/.well-known/...`) and enrollment (`/v1/auth/login`)
endpoints. Used by `tests/test_integration_docker.py` to drive the
generated SDK against a real network endpoint inside a container.

Run locally::

    poetry run python -m examples.pneumatic_bear_poker.firmware_sim.app

Run in Docker::

    docker build -t pneumatic-bear-poker-sim examples/pneumatic_bear_poker/firmware_sim
    docker run --rm -p 8080:8080 pneumatic-bear-poker-sim
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from flask import Flask, jsonify, request
from flask.wrappers import Response

VERSION = "2.4.1"
SERVER_HEADER = f"PneumaticBearPoker/{VERSION}"
"""Matches the manifest's ``discovery.http.server_header_prefix``."""


# Single in-memory token; the test only enrolls once per container lifetime.
_VALID_TOKEN = secrets.token_hex(16)


def create_app() -> Flask:
    """Build and return the configured Flask app."""
    app = Flask(__name__)

    # ---- discovery probe --------------------------------------------------
    @app.get("/.well-known/pneumatic-bear-poker")
    def well_known() -> Response:
        """Discovery probe target -- HttpScanner inspects the Server header."""
        return jsonify({"device": "pneumatic_bear_poker", "version": VERSION})

    # ---- enrollment -------------------------------------------------------
    @app.post("/v1/auth/login")
    def login() -> Response:
        """Hand back a bearer token. Real firmware would validate credentials."""
        # The default HttpEnrollment posts `{}` -- accept anything.
        _ = request.get_json(silent=True)
        return jsonify({"token": _VALID_TOKEN})

    # ---- commands ---------------------------------------------------------
    @app.post("/v1/poker/deploy")
    def poker_deploy() -> Response:
        body = _json_body()
        pressure = int(body.get("pressure_psi", 0))
        return jsonify({"delivered_psi": pressure})

    @app.post("/v1/safety/emergency_retract")
    def safety_retract() -> Response:
        return jsonify({"retracted": True})

    @app.post("/v1/power/on")
    def power_on() -> Response:
        return jsonify({"powered": True})

    @app.post("/v1/power/off")
    def power_off() -> Response:
        body = _json_body()
        drain = bool(body.get("drain_pressure", True))
        return jsonify({"powered": False, "residual_psi": 0 if drain else 12})

    @app.get("/v1/logs")
    def logs_download() -> Response:
        # `query_from_request: true` in the manifest → fields land in query string.
        max_lines = int(request.args.get("max_lines", 1000))
        since = request.args.get("since_sequence")
        start = int(since) if since is not None else 0
        lines = tuple(f"line {i}" for i in range(start, start + min(max_lines, 5)))
        return jsonify({"lines": list(lines), "next_sequence": start + len(lines)})

    return app


def _json_body() -> dict[str, Any]:
    """Return the request's JSON body as a dict (empty dict if absent)."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    from waitress import serve

    # `ident=` controls the `Server` HTTP header. Discovery matches on
    # `discovery.http.server_header_prefix` from the manifest.
    serve(create_app(), host="0.0.0.0", port=port, ident=SERVER_HEADER)  # firmware sim
