# p4n4-api

> Unified **REST API gateway** for the P4N4 platform — written in Python.

`p4n4-api` is the central HTTP API layer for the P4N4 IoT + GenAI + Edge AI platform. It exposes a single, versioned, authenticated REST surface over the otherwise fragmented set of internal services (InfluxDB, MQTT, Ollama, Letta, Edge Impulse Runner), making it easy to build dashboards, mobile apps, or external integrations without touching each service directly.

Part of the [p4n4](https://github.com/raisga/p4n4) platform — an EdgeAI + GenAI integration platform for IoT deployments.

---

## Status

**v0.1** implements a read-only project/stack surface built on
[`p4n4-lib`](https://github.com/raisga/p4n4-lib) (manifest, layout, validation, and
Compose status — both flat and multi-layer project layouts):

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/v1/project` | Manifest, layout (`flat`/`multi`), and per-stack directories |
| `GET` | `/api/v1/project/validate` | Run `p4n4_lib.validate` checks; returns `{ok, passed, errors}` |
| `GET` | `/api/v1/stacks` | Compose service status per stack |
| `GET` | `/api/v1/stacks/{stack}` | One stack's service status (404 if not enabled) |
| `GET` | `/swagger-ui`, `/openapi.json` | Interactive docs / OpenAPI spec |

Everything else in this README (auth, device registry, telemetry, SSE, agents, MQTT,
metrics) is the **design target**, not yet implemented. State-changing endpoints
(stack up/down, secret rotation) are deliberately deferred until auth lands.

---

## Table of Contents

- [Architecture](#architecture)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Project Structure](#project-structure)
- [Development](#development)
- [Default Port](#default-port)
- [Network Requirements](#network-requirements)
- [Security](#security)
- [Resources](#resources)
- [License](#license)

---

## Architecture

```
  External clients (dashboards, mobile apps, scripts)
                        │
                        ▼ HTTP / REST  (port 8000)
              ┌─────────────────────┐
              │      p4n4-api       │
              │  (Python · FastAPI) │
              │                     │
              │  ┌───────────────┐  │
              │  │  Auth (JWT)   │  │
              │  └───────────────┘  │
              │  ┌───────────────┐  │
              │  │ Device Reg.   │  │
              │  │  (SQLite)     │  │
              │  └───────────────┘  │
              └──────────┬──────────┘
                         │ p4n4-net (Docker bridge)
         ┌───────────────┼───────────────────┐
         ▼               ▼                   ▼
  ┌─────────────┐ ┌─────────────┐   ┌──────────────┐
  │  p4n4-iot   │ │  p4n4-ai    │   │  p4n4-edge   │
  │  ─────────  │ │  ─────────  │   │  ──────────  │
  │  MQTT       │ │  Ollama     │   │  EI Runner   │
  │  InfluxDB   │ │  Letta      │   └──────────────┘
  └─────────────┘ └─────────────┘
```

**Data flow:**
1. Clients authenticate and receive a JWT.
2. Requests are routed to the relevant upstream service (InfluxDB, MQTT, Ollama, Letta, or the Edge runner).
3. Telemetry ingested via the API is written to InfluxDB **and** published to MQTT, so Node-RED flows trigger normally.
4. A live telemetry SSE stream is backed by a persistent MQTT subscription.

---

## Features

- **Unified auth** — API key → JWT (HS256). Three roles: `device`, `operator`, `admin`.
- **Device registry** — CRUD for devices; API keys hashed with argon2id; stored in SQLite.
- **Telemetry ingest** — batch JSON → InfluxDB line protocol + MQTT publish.
- **Telemetry query** — Flux proxy against InfluxDB with simple query-param interface.
- **Live SSE stream** — `GET /api/v1/telemetry/stream` delivers real-time readings over Server-Sent Events.
- **Inference** — proxy to the Edge Impulse runner (`POST /api/v1/inference`).
- **AI agents** — Ollama one-shot generation and Letta stateful chat.
- **MQTT publish** — `POST /api/v1/mqtt/publish` for arbitrary messages.
- **Stack health** — `GET /api/v1/stacks` pings all platform services and reports status.
- **OpenAPI 3.1** — `/openapi.json` + Swagger UI at `/swagger-ui`.
- **Prometheus metrics** — `/metrics` endpoint with request counters, latency histograms, and upstream call stats.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) v24+ (with Compose v2)
- `p4n4-iot` running (provides `p4n4-net`, MQTT, and InfluxDB)

For local development only:

- [Python](https://www.python.org/downloads/) 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`

---

## Getting Started

1. **Clone and install** (with [`p4n4-lib`](https://github.com/raisga/p4n4-lib))

   ```bash
   git clone https://github.com/raisga/p4n4-api.git
   cd p4n4-api
   uv venv
   uv pip install "p4n4-lib @ git+https://github.com/raisga/p4n4-lib.git" -e .
   # monorepo: uv pip install -e ../../core/lib -e .
   ```

2. **Point it at a p4n4 project** (scaffolded by `p4n4 init`; flat or multi-layer)

   ```bash
   export P4N4_PROJECT_DIR=~/projects/my-p4n4-project
   ```

3. **Start the API**

   ```bash
   uv run p4n4-api
   # or: uv run uvicorn p4n4_api.main:app --reload --port 8000
   ```

4. **Verify it is running**

   ```bash
   curl http://localhost:8000/health
   # {"status":"ok"}

   curl http://localhost:8000/api/v1/project
   curl http://localhost:8000/api/v1/project/validate
   curl http://localhost:8000/api/v1/stacks

   # Open the interactive API docs
   open http://localhost:8000/swagger-ui
   ```

---

## Configuration

All configuration is read from environment variables. Currently used:

| Variable | Description |
|---|---|
| `P4N4_PROJECT_DIR` | p4n4 project directory to serve (walks up to `.p4n4.json`; default: the server's cwd) |
| `P4N4_API_HOST` | Bind address (default: `127.0.0.1`) |
| `P4N4_API_PORT` | HTTP listen port (default: `8000`) |

Planned (for the upstream-proxy features below): `P4N4_API_JWT_SECRET`, `INFLUXDB_URL`,
`INFLUXDB_TOKEN`, `MQTT_HOST`, `MQTT_USER`/`MQTT_PASSWORD`, `OLLAMA_URL`, `LETTA_URL`,
`LETTA_SERVER_PASSWORD`, `EDGE_RUNNER_URL`, `P4N4_API_DATABASE_URL`.

---

## API Reference

Base path: `/api/v1`
Authentication: `Authorization: Bearer <jwt>` (except public endpoints)

### Public endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe (checks DB + MQTT) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/openapi.json` | OpenAPI 3.1 spec |
| `GET` | `/swagger-ui` | Swagger UI |

### Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/token` | Exchange API key for JWT |
| `POST` | `/api/v1/auth/refresh` | Rotate access token |

### Stack health (operator+)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/stacks` | Health of all platform stacks |
| `GET` | `/api/v1/stacks/{stack}` | Health of one stack (`iot`, `ai`, `edge`) |

### Devices (admin for writes, operator for reads)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/devices` | List devices (paginated) |
| `POST` | `/api/v1/devices` | Register a new device |
| `GET` | `/api/v1/devices/{id}` | Get device by ID |
| `PATCH` | `/api/v1/devices/{id}` | Update device metadata |
| `DELETE` | `/api/v1/devices/{id}` | Deregister device |
| `GET` | `/api/v1/devices/{id}/key` | Rotate API key |

### Telemetry

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/telemetry` | device | Ingest readings (→ InfluxDB + MQTT) |
| `GET` | `/api/v1/telemetry` | operator | Query historical data |
| `GET` | `/api/v1/telemetry/stream` | operator | SSE live stream |

### Inference (operator+)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/inference` | Submit feature vector for Edge Impulse inference |
| `GET` | `/api/v1/inference/results` | Query recent results from InfluxDB `ai_events` |

### AI agents (operator+)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/agents` | List Letta agents |
| `POST` | `/api/v1/agents/{id}/chat` | Chat with a Letta agent |
| `POST` | `/api/v1/agents/generate` | One-shot Ollama generation |

### MQTT (operator+)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/mqtt/publish` | Publish a message to a topic |

See the [DESIGN.md](DESIGN.md) for complete request/response schemas, error codes, and middleware documentation.

---

## Project Structure

Current (v0.1):

```
p4n4-api/
├── pyproject.toml
├── p4n4_api/
│   ├── main.py              # FastAPI app factory + `p4n4-api` entrypoint
│   ├── config.py            # Settings from environment variables
│   ├── deps.py              # Project resolution dependency (p4n4_lib.manifest)
│   └── routes/              # One APIRouter per API group
│       ├── health.py        # GET /health
│       ├── project.py       # GET /api/v1/project, /project/validate
│       └── stacks.py        # GET /api/v1/stacks, /stacks/{stack}
└── tests/
```

Planned additions as the upstream-proxy features land: `auth/` (JWT), `clients/`
(async HTTP/MQTT clients per upstream service), `models/` (Pydantic schemas),
`db/` + `alembic/` (device registry), `Dockerfile` + `docker-compose.yml`.

---

## Development

```bash
# Install dependencies (see Getting Started for the p4n4-lib install)
uv pip install -e ".[dev]"

# Run locally against a scaffolded project
P4N4_PROJECT_DIR=~/projects/my-p4n4-project uv run uvicorn p4n4_api.main:app --reload --port 8000

# Tests
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .
```

Tests use synthetic projects and a stubbed Compose client, so they run without
Docker or live services. Fixtures live in `tests/conftest.py`.

---

## Default Port

| Service | Port |
|---|---|
| p4n4-api | `8000` |

This does not conflict with any other service in the P4N4 platform.

---

## Network Requirements

In v0.1 the API runs **on the host** (not in a container): the stack-status endpoints
shell out to `docker compose ps` inside each stack directory, so the host needs the
Docker CLI and access to the Docker daemon.

The containerized deployment (attaching to `p4n4-net` as an external network, with
`docker compose up -d` in this repo and `p4n4 up --api` in the CLI) is planned along
with the upstream-proxy features.

---

## Security

- **JWT** — HS256 signed tokens with role claims (`device`, `operator`, `admin`). Access tokens expire in 1 hour; refresh tokens in 7 days.
- **API keys** — stored as argon2id hashes; plaintext shown only once at device registration.
- **Secrets** — never stored in the database; all upstream credentials are injected via environment variables.
- **Rate limiting** — per-subject token-bucket (in-memory); no Redis dependency.
- **CORS** — permissive in development, allowlist in production (set via `P4N4_API_CORS_ORIGINS`).
- **Port exposure** — for production, remove the `8000` host-port binding and front with a reverse proxy (nginx, Caddy, Traefik).

---

## Resources

- [p4n4 Platform](https://github.com/raisga/p4n4) — umbrella repo and architecture docs
- [DESIGN.md](DESIGN.md) — full API design document (tech stack, schemas, milestones)
- [p4n4-lib](https://github.com/raisga/p4n4-lib) — shared library (manifest, layout, validation, Compose wrappers) consumed by this package
- [p4n4-iot](https://github.com/raisga/p4n4-iot) — IoT stack (MQTT, InfluxDB, Node-RED, Grafana)
- [p4n4-ai](https://github.com/raisga/p4n4-ai) — GenAI stack (Ollama, Letta, n8n)
- [p4n4-edge](https://github.com/raisga/p4n4-edge) — Edge AI stack (Edge Impulse runner)
- [FastAPI](https://fastapi.tiangolo.com/) — Python web framework (OpenAPI 3.1 built-in)
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — settings management via environment variables

---

## License

This project is licensed under the [MIT License](LICENSE).
