# p4n4-api

> Unified **REST API gateway** for the P4N4 platform — written in Rust.

`p4n4-api` is the central HTTP API layer for the P4N4 IoT + GenAI + Edge AI platform. It exposes a single, versioned, authenticated REST surface over the otherwise fragmented set of internal services (InfluxDB, MQTT, Ollama, Letta, Edge Impulse Runner), making it easy to build dashboards, mobile apps, or external integrations without touching each service directly.

Part of the [p4n4](https://github.com/raisga/p4n4) platform — an EdgeAI + GenAI integration platform for IoT deployments.

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
              │  (Rust · Axum)      │
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

- [Rust](https://rustup.rs/) stable 1.80+
- `cargo` (included with Rust)

---

## Getting Started

1. **Clone the repository**

   ```bash
   git clone https://github.com/raisga/p4n4-api.git
   cd p4n4-api
   ```

2. **Configure environment variables**

   ```bash
   cp .env.example .env
   # Edit .env — set INFLUXDB_TOKEN, MQTT credentials, and JWT secret
   ```

3. **Ensure `p4n4-net` exists** (skip if p4n4-iot is already running)

   ```bash
   docker network create p4n4-net
   ```

4. **Start the API**

   ```bash
   docker compose up -d
   ```

5. **Verify it is running**

   ```bash
   curl http://localhost:8000/health
   # {"status":"ok"}

   # Open the interactive API docs
   open http://localhost:8000/swagger-ui
   ```

---

## Configuration

All configuration is read from environment variables (or a `.env` file). Copy `.env.example` and fill in the values that match your running `p4n4-iot` and `p4n4-ai` stacks.

Key variables:

| Variable | Description |
|---|---|
| `P4N4_API_PORT` | HTTP listen port (default: `8000`) |
| `P4N4_API_JWT_SECRET` | Secret for HS256 JWT signing (256-bit hex) |
| `INFLUXDB_URL` | InfluxDB base URL (`http://p4n4-influxdb:8086`) |
| `INFLUXDB_TOKEN` | InfluxDB API token — must match `p4n4-iot` `.env` |
| `MQTT_HOST` | Mosquitto host (`p4n4-mqtt`) |
| `MQTT_USER` / `MQTT_PASSWORD` | MQTT credentials — must match `p4n4-iot` `.env` |
| `OLLAMA_URL` | Ollama base URL (`http://p4n4-ollama:11434`) |
| `LETTA_URL` | Letta base URL (`http://p4n4-letta:8283`) |
| `LETTA_SERVER_PASSWORD` | Letta bearer token |
| `EDGE_RUNNER_URL` | Edge Impulse runner URL (`http://p4n4-edge-runner:8080`) |
| `P4N4_API_DATABASE_URL` | SQLite path (`sqlite:///data/p4n4-api.db`) |

See `.env.example` for the full list.

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

```
p4n4-api/
├── Cargo.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── migrations/
│   └── 0001_create_devices.sql
├── src/
│   ├── main.rs              # startup: config → state → router → serve
│   ├── config.rs            # Settings struct
│   ├── state.rs             # AppState (Arc) shared across handlers
│   ├── error.rs             # ApiError → (StatusCode, JSON) IntoResponse
│   ├── auth/                # JWT claims, middleware extractor, token helpers
│   ├── routes/              # One module per API group
│   ├── clients/             # Thin HTTP/MQTT clients for each upstream service
│   ├── models/              # Request/response types (serde)
│   └── db/                  # SQLx queries for the device registry
└── tests/
    ├── integration/
    └── unit/
```

---

## Development

```bash
# Build
cargo build

# Run locally (requires .env or exported env vars)
cargo run

# Tests
cargo test

# Lint
cargo clippy -- -D warnings

# Format
cargo fmt
```

The API requires a reachable MQTT broker and InfluxDB instance (or mock environment).
For isolated unit tests, use the test helpers in `tests/common/`.

---

## Default Port

| Service | Port |
|---|---|
| p4n4-api | `8000` |

This does not conflict with any other service in the P4N4 platform.

---

## Network Requirements

`p4n4-api` attaches to `p4n4-net` as an **external** network. Start `p4n4-iot` first (or create the network manually):

```bash
# Option 1 — p4n4-iot already running
docker compose up -d   # in p4n4-api/

# Option 2 — standalone
docker network create p4n4-net
docker compose up -d

# Option 3 — via CLI (once p4n4 up --api is implemented)
p4n4 up --api
```

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
- [p4n4-lib](https://github.com/raisga/p4n4-lib) — shared Rust library (models, clients, auth) consumed by this crate
- [p4n4-iot](https://github.com/raisga/p4n4-iot) — IoT stack (MQTT, InfluxDB, Node-RED, Grafana)
- [p4n4-ai](https://github.com/raisga/p4n4-ai) — GenAI stack (Ollama, Letta, n8n)
- [p4n4-edge](https://github.com/raisga/p4n4-edge) — Edge AI stack (Edge Impulse runner)
- [Axum](https://github.com/tokio-rs/axum) — Rust web framework
- [utoipa](https://github.com/juhaku/utoipa) — OpenAPI 3.1 generation for Rust

---

## License

This project is licensed under the [MIT License](LICENSE).
