# syntax=docker/dockerfile:1.6
# Pneumatic Bear Poker firmware simulator -- HTTP-only.
# Built and started by tests/test_integration_docker.py via testcontainers.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN pip install --no-cache-dir "flask>=3.0,<4" "waitress>=3,<4"

COPY app.py /app/app.py

EXPOSE 8080

# Waitress is used (instead of gunicorn) because it exposes a clean
# `ident=` knob for the HTTP `Server` header -- which our discovery
# probe matches against `discovery.http.server_header_prefix`.
CMD ["python", "/app/app.py"]
