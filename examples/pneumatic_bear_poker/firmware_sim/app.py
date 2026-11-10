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

# Regenerated on every start, so restarting the sim invalidates previously saved tokens (-> HTTP 401).
_VALID_TOKEN = secrets.token_hex(16)

# Reachable without a token: discovery and the login that issues one.
_OPEN_PATHS = frozenset({"/.well-known/pneumatic-bear-poker", "/v1/auth/login"})

# poker.deploy refuses anything higher (HTTP 400); the internal-only poker.overdrive has no ceiling.
_DEPLOY_CEILING_PSI = 100


def create_app() -> Flask:
    """Build and return the configured Flask app."""
    app = Flask(__name__)
    app.before_request(_require_token)

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
    _register_power_and_arm(app)

    @app.get("/v1/logs")
    def logs_download() -> Response:
        # `query_from_request: true` in the manifest → fields land in query string.
        max_lines = int(request.args.get("max_lines", 1000))
        since = request.args.get("since_sequence")
        start = int(since) if since is not None else 0
        lines = tuple(f"line {i}" for i in range(start, start + min(max_lines, 5)))
        return jsonify({"lines": list(lines), "next_sequence": start + len(lines)})

    _register_settings(app)
    return app


def _register_settings(app: Flask) -> None:
    """Register the `settings.poke_intensity` attribute routes (read / write)."""
    state = {"poke_intensity": 5}

    @app.get("/v1/settings/poke_intensity")
    def read_poke_intensity() -> Response:
        return jsonify({"level": state["poke_intensity"]})

    @app.put("/v1/settings/poke_intensity")
    def write_poke_intensity() -> Response:
        state["poke_intensity"] = int(_json_body().get("level", 0))
        return jsonify({"level": state["poke_intensity"]})  # echoed value is the write-ack


def _register_power_and_arm(app: Flask) -> None:
    """Register the commands that drive the power / arm state machine (docs: Manifest -> Device State Machine)."""
    # Power and arm position are independent: nothing ever moves the arm except deploy / overdrive / retract.
    machine = {"powered": False, "extended": False, "held_psi": 0}

    @app.post("/v1/poker/deploy")
    def poker_deploy() -> Response:
        return _extend(machine, _DEPLOY_CEILING_PSI)

    @app.post("/v1/poker/overdrive")
    def poker_overdrive() -> Response:
        return _extend(machine, None)

    @app.post("/v1/poker/retract")
    def poker_retract() -> Response:
        if not machine["powered"]:
            return _rejected("not powered")
        machine["extended"] = False
        return jsonify({"retracted": True})

    @app.post("/v1/power/on")
    def power_on() -> Response:
        machine["powered"] = True
        return jsonify({"powered": True})

    @app.post("/v1/power/off")
    def power_off() -> Response:
        drain = bool(_json_body().get("drain_pressure", True))
        machine["powered"] = False
        return jsonify({"powered": False, "residual_psi": 0 if drain else 12})


def _extend(machine: dict[str, Any], ceiling_psi: int | None) -> Response:
    """Extend the arm (deploy / overdrive): refused while unpowered, a no-op when already extended."""
    if not machine["powered"]:
        return _rejected("not powered")
    if not machine["extended"]:
        pressure = int(_json_body().get("pressure_psi", 0))
        if ceiling_psi is not None and pressure > ceiling_psi:
            return _rejected(f"{pressure} psi exceeds the {ceiling_psi} psi ceiling", status=400)
        machine["held_psi"] = pressure
        machine["extended"] = True
    return jsonify({"delivered_psi": machine["held_psi"]})


def _require_token() -> Response | None:
    """Refuse any request without the current bearer token (HTTP 401), except discovery and login."""
    if request.path in _OPEN_PATHS or request.headers.get("Authorization") == f"Bearer {_VALID_TOKEN}":
        return None
    response = jsonify({"error": "missing or invalid bearer token"})
    response.status_code = 401
    return response


def _json_body() -> dict[str, Any]:
    """Return the request's JSON body as a dict (empty dict if absent)."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _rejected(reason: str, *, status: int = 409) -> Response:
    """Refuse a command (409 = invalid in the current state, 400 = bad argument); both classify as REJECTED."""
    response = jsonify({"error": reason})
    response.status_code = status
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app = create_app()
    try:
        from waitress import serve
    except ImportError:
        # A plain `make install` venv has Flask but not waitress; its built-in
        # server is fine for a local sim. Override Werkzeug's Server header so
        # discovery (which matches on `server_header_prefix`) recognizes it.
        from werkzeug.serving import WSGIRequestHandler

        WSGIRequestHandler.server_version = SERVER_HEADER
        WSGIRequestHandler.sys_version = ""
        app.run(host="127.0.0.1", port=port)
    else:
        serve(app, host="0.0.0.0", port=port, ident=SERVER_HEADER)  # firmware sim
