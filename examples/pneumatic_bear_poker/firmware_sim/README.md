# Pneumatic Bear Poker firmware simulator

A tiny [Flask](https://flask.palletsprojects.com/) app that impersonates the HTTP slice of a Pneumatic Bear Poker so the
generated SDK can be driven against a real network endpoint instead of an in-process fake.

Used by:
- `tests/test_integration_docker.py` — full lifecycle integration test
  (discover → enroll → save → reconnect → dispatch) over real aiohttp
  against a containerized firmware sim, orchestrated with
  [`testcontainers-python`](https://testcontainers-python.readthedocs.io/).
- `make integration-test` — runs the marker-gated integration suite.

## Run the container by hand

```sh
docker build -t pneumatic-bear-poker-sim examples/pneumatic_bear_poker/firmware_sim
docker run --rm -p 8080:8080 pneumatic-bear-poker-sim
```

Then poke at it:

```sh
curl -i http://localhost:8080/.well-known/pneumatic-bear-poker
curl -X POST http://localhost:8080/v1/auth/login -d '{}' -H 'Content-Type: application/json'
curl -X POST http://localhost:8080/v1/poker/deploy \
     -d '{"pressure_psi": 42}' -H 'Content-Type: application/json'
```

## Run the Flask app without Docker

```sh
poetry run python -m examples.pneumatic_bear_poker.firmware_sim.app
```
