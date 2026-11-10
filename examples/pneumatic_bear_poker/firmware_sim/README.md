# Pneumatic Bear Poker firmware simulator

A tiny [Flask](https://flask.palletsprojects.com/) app that impersonates the HTTP slice of a Pneumatic Bear Poker so the
generated SDK can be driven against a real network endpoint instead of an in-process fake.

Used by:
- `tests/test_integration_docker.py` — full lifecycle integration test
  (discover → enroll → save → reconnect → dispatch) over real aiohttp
  against a containerized firmware sim, orchestrated with
  [`testcontainers-python`](https://testcontainers-python.readthedocs.io/).
- `make test-integration` — runs the marker-gated integration suite.

The sim tracks power and arm position, starting powered off with the arm retracted; deploy / retract are refused with
HTTP 409 until it is powered on. See the
[Device State Machine](../../../docs/concepts/manifest.md#device-state-machine) diagram.

Every route except the discovery probe (`/v1/status`) and `/v1/auth/login` requires the bearer token from that login
(HTTP 401 otherwise). The
token is regenerated each time the sim starts, so a restart invalidates previously saved credentials.

## Run the container by hand

```sh
docker build -t pneumatic-bear-poker-sim examples/pneumatic_bear_poker/firmware_sim
docker run --rm -p 8080:8080 pneumatic-bear-poker-sim
```

Then poke at it:

```sh
curl -i http://localhost:8080/v1/status
TOKEN=$(curl -s -X POST http://localhost:8080/v1/auth/login -d '{}' -H 'Content-Type: application/json' \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Bearer $TOKEN"
curl -X POST http://localhost:8080/v1/power/on -d '{}' -H 'Content-Type: application/json' -H "$AUTH"
curl -X POST http://localhost:8080/v1/poker/deploy \
     -d '{"pressure_psi": 42}' -H 'Content-Type: application/json' -H "$AUTH"
curl -X POST http://localhost:8080/v1/poker/retract -d '{}' -H 'Content-Type: application/json' -H "$AUTH"
```

## Run the Flask app without Docker

```sh
poetry run python -m examples.pneumatic_bear_poker.firmware_sim.app
```
