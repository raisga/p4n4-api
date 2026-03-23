# p4n4-api — Rust API Design Draft

> **Status:** Draft v0.1 — 2026-03-15
> **Author:** p4n4 core team
> **Scope:** Unified REST API gateway for the P4N4 platform (IoT + GenAI + Edge)

---

## 1. Purpose & Goals

`p4n4-api` is the central HTTP API gateway for the P4N4 platform. It provides a **single, authenticated, versioned REST surface** over the otherwise fragmented set of internal services (InfluxDB, MQTT, Ollama, Letta, n8n, Edge Impulse Runner).

### Goals

- **Unify** — one endpoint, one auth scheme, one OpenAPI spec for all platform capabilities.
- **Proxy** — thin translation layer, not a data store (except for device registry metadata).
- **Real-time** — Server-Sent Events (SSE) stream for live telemetry and inference results.
- **Secure** — JWT-based auth with role claims; no credentials exposed to API consumers.
- **Observable** — structured JSON logs (`tracing`), Prometheus metrics, health endpoints.
- **Containerised** — ships as a Docker image; joins `p4n4-net` like every other stack.

### Non-Goals

- Not a replacement for Grafana dashboards or Node-RED flows.
- Not a message broker — MQTT remains the event bus; the API is a convenience proxy.
- Not a long-term data store — InfluxDB owns all time-series data.

---

## 2. Technology Stack

| Concern | Crate / Tool | Rationale |
|---|---|---|
| Web framework | [`axum`](https://github.com/tokio-rs/axum) | Tower-native, zero-cost abstractions, great extractor ergonomics |
| Async runtime | [`tokio`](https://tokio.rs) | Axum dependency; industry standard |
| HTTP client | [`reqwest`](https://github.com/seanmonstar/reqwest) | Async, TLS, cookie-free, easy JSON bodies |
| MQTT client | [`rumqttc`](https://github.com/bytebeamio/rumqtt) | Pure-Rust async MQTT v3/v5, clean API |
| Serialization | [`serde`](https://serde.rs) + `serde_json` | Ubiquitous; zero-copy deserialisation |
| OpenAPI spec | [`utoipa`](https://github.com/juhaku/utoipa) + `utoipa-axum` | Derive-macro-based OpenAPI 3.1 generation |
| Swagger UI | `utoipa-swagger-ui` | Bundled UI, no CDN required |
| Auth (JWT) | [`jsonwebtoken`](https://github.com/Keats/jsonwebtoken) | HS256 / RS256, claims validation |
| Middleware | [`tower-http`](https://github.com/tower-rs/tower-http) | CORS, compression, request tracing, timeouts |
| Logging | [`tracing`](https://github.com/tokio-rs/tracing) + `tracing-subscriber` | Structured JSON logs; integrates with tokio |
| Metrics | [`axum-prometheus`](https://github.com/Ptrskay3/axum-prometheus) | Prometheus endpoint via Tower layer |
| Config | [`config`](https://github.com/mehcode/config-rs) + `dotenvy` | Layered env/file config, `.env` support |
| Validation | [`validator`](https://github.com/Keats/validator) | Derive-based request body validation |
| Error handling | [`thiserror`](https://github.com/dtolnay/thiserror) | Ergonomic typed errors |
| UUIDs | [`uuid`](https://github.com/uuid-rs/uuid) v1 (time-based) | Sortable device IDs |
| Time | [`chrono`](https://github.com/chronotope/chrono) | RFC 3339 timestamps, InfluxDB line protocol |
| SQLite (registry) | [`sqlx`](https://github.com/launchbadge/sqlx) (SQLite feature) | Lightweight device metadata store; no extra service |

### Compiler & Edition

- Rust **stable** (MSRV 1.80+)
- Edition **2021**
- Cross-compile target for the container: `x86_64-unknown-linux-musl` (static binary)

---

## 3. Project Layout

```
p4n4-api/
├── Cargo.toml
├── Cargo.lock                    # committed (binary crate)
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── migrations/                   # SQLx migration files
│   └── 0001_create_devices.sql
├── src/
│   ├── main.rs                   # startup: config → state → router → serve
│   ├── config.rs                 # Settings struct (config-rs)
│   ├── state.rs                  # AppState (Arc<Inner>) shared across handlers
│   ├── error.rs                  # ApiError → (StatusCode, JSON) IntoResponse
│   ├── auth/
│   │   ├── mod.rs
│   │   ├── claims.rs             # JWT Claims struct + role enum
│   │   ├── middleware.rs         # axum extractor: RequireAuth<Role>
│   │   └── tokens.rs             # encode / decode / refresh helpers
│   ├── routes/
│   │   ├── mod.rs                # assemble Router, attach layers
│   │   ├── health.rs             # GET /health, GET /ready
│   │   ├── auth.rs               # POST /api/v1/auth/token, /refresh
│   │   ├── stacks.rs             # GET /api/v1/stacks, /{stack}/health
│   │   ├── devices.rs            # CRUD /api/v1/devices
│   │   ├── telemetry.rs          # GET|POST /api/v1/telemetry (+SSE stream)
│   │   ├── inference.rs          # POST /api/v1/inference, GET /results
│   │   ├── agents.rs             # POST /api/v1/agents/chat, GET /agents
│   │   └── mqtt.rs               # POST /api/v1/mqtt/publish
│   ├── clients/
│   │   ├── mod.rs
│   │   ├── influxdb.rs           # InfluxDB v2 HTTP client (write + query)
│   │   ├── mqtt.rs               # rumqttc async client wrapper
│   │   ├── ollama.rs             # Ollama OpenAI-compat client
│   │   ├── letta.rs              # Letta REST client
│   │   └── edge.rs               # Edge Impulse Runner client
│   ├── models/
│   │   ├── mod.rs
│   │   ├── device.rs             # Device, NewDevice, DeviceStatus
│   │   ├── telemetry.rs          # TelemetryPoint, TelemetryQuery, TelemetryResponse
│   │   ├── inference.rs          # InferenceRequest, InferenceResult
│   │   ├── agent.rs              # ChatMessage, ChatRequest, ChatResponse
│   │   └── common.rs             # PaginatedResponse<T>, ErrorResponse
│   └── db/
│       ├── mod.rs
│       └── devices.rs            # Device registry queries (sqlx)
└── tests/
    ├── common/
    │   └── helpers.rs            # test app builder, mock clients
    ├── integration/
    │   ├── test_health.rs
    │   ├── test_devices.rs
    │   └── test_telemetry.rs
    └── unit/
        ├── test_jwt.rs
        └── test_influx_line_protocol.rs
```

---

## 4. Configuration

All configuration is read from environment variables (or a `.env` file via `dotenvy`).

```bash
# .env.example

# ── Server ─────────────────────────────────────────────────────────────────
P4N4_API_HOST=0.0.0.0
P4N4_API_PORT=8000
P4N4_API_LOG_LEVEL=info            # trace|debug|info|warn|error
P4N4_API_ENV=production            # development|production

# ── Auth ────────────────────────────────────────────────────────────────────
P4N4_API_JWT_SECRET=<random-256-bit-hex>
P4N4_API_JWT_EXPIRY_SECONDS=3600   # 1 hour access token
P4N4_API_JWT_REFRESH_EXPIRY_SECONDS=604800  # 7 days

# ── Device Registry (SQLite) ────────────────────────────────────────────────
P4N4_API_DATABASE_URL=sqlite:///data/p4n4-api.db

# ── p4n4-iot (InfluxDB) ─────────────────────────────────────────────────────
INFLUXDB_URL=http://p4n4-influxdb:8086
INFLUXDB_TOKEN=<influxdb-admin-token>
INFLUXDB_ORG=ming
INFLUXDB_BUCKET=raw_telemetry
INFLUXDB_BUCKET_AI_EVENTS=ai_events
INFLUXDB_BUCKET_PROCESSED=processed_metrics

# ── p4n4-iot (MQTT) ─────────────────────────────────────────────────────────
MQTT_HOST=p4n4-mqtt
MQTT_PORT=1883
MQTT_USER=<mqtt-user>
MQTT_PASSWORD=<mqtt-password>
MQTT_CLIENT_ID=p4n4-api-gateway

# ── p4n4-ai (Ollama) ────────────────────────────────────────────────────────
OLLAMA_URL=http://p4n4-ollama:11434
OLLAMA_DEFAULT_MODEL=llama3.2

# ── p4n4-ai (Letta) ─────────────────────────────────────────────────────────
LETTA_URL=http://p4n4-letta:8283
LETTA_SERVER_PASSWORD=<letta-password>

# ── p4n4-edge (Edge Runner) ─────────────────────────────────────────────────
EDGE_RUNNER_URL=http://p4n4-edge-runner:8080
```

The `config.rs` module uses `config-rs` to layer:

1. `config/default.toml` (bundled defaults)
2. `config/{env}.toml` (environment-specific overrides)
3. Environment variables with prefix `P4N4_API_`

---

## 5. AppState

All shared resources (clients, DB pool, config) are held in a single `Arc<AppState>` injected into Axum via `.with_state()`.

```rust
// src/state.rs (pseudocode sketch)

pub struct AppState {
    pub config: Arc<Settings>,
    pub db: SqlitePool,
    pub influx: InfluxClient,
    pub mqtt: MqttClient,
    pub ollama: OllamaClient,
    pub letta: LettaClient,
    pub edge: EdgeClient,
    // SSE broadcast channel for live telemetry
    pub telemetry_tx: broadcast::Sender<TelemetryPoint>,
}
```

The MQTT client subscribes to wildcard topic `+/+/+/+` on startup and broadcasts
inbound messages to `telemetry_tx`. SSE handler at `/api/v1/telemetry/stream`
subscribes a receiver from `telemetry_tx`.

---

## 6. API Reference

Base path: `/api/v1`
Content-Type: `application/json`
Auth: `Authorization: Bearer <jwt>` (except `/health`, `/ready`, `/openapi.json`, `/swagger-ui`)

### 6.1 Utility

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Liveness probe — always `200 OK` |
| `GET` | `/ready` | None | Readiness probe — checks DB + MQTT |
| `GET` | `/metrics` | None | Prometheus metrics |
| `GET` | `/openapi.json` | None | OpenAPI 3.1 spec |
| `GET` | `/swagger-ui` | None | Swagger UI |

### 6.2 Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/auth/token` | None | Exchange API key for JWT (access + refresh) |
| `POST` | `/api/v1/auth/refresh` | Refresh JWT | Rotate access token |

**`POST /api/v1/auth/token`**

```json
// Request
{ "api_key": "p4n4_<base62-40-chars>" }

// Response 200
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

JWT claims:

```json
{
  "sub": "device:T001",        // or "user:admin"
  "role": "device",            // device | operator | admin
  "iat": 1741996800,
  "exp": 1742000400
}
```

Roles:

| Role | Capabilities |
|------|-------------|
| `device` | Publish telemetry, read own device record |
| `operator` | All device reads, telemetry reads, inference, agents |
| `admin` | Full access including stack management |

### 6.3 Stack Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/stacks` | operator | Summary of all stack health |
| `GET` | `/api/v1/stacks/{stack}` | operator | Health of one stack |

`{stack}` values: `iot`, `ai`, `edge`

**`GET /api/v1/stacks`**

```json
// Response 200
{
  "stacks": {
    "iot": {
      "status": "healthy",
      "services": {
        "mqtt":     { "status": "healthy", "latency_ms": 2  },
        "influxdb": { "status": "healthy", "latency_ms": 8  },
        "node-red": { "status": "healthy", "latency_ms": 14 },
        "grafana":  { "status": "healthy", "latency_ms": 11 }
      }
    },
    "ai": {
      "status": "degraded",
      "services": {
        "ollama": { "status": "healthy", "latency_ms": 5  },
        "letta":  { "status": "unreachable", "latency_ms": null },
        "n8n":    { "status": "healthy", "latency_ms": 22 }
      }
    },
    "edge": {
      "status": "healthy",
      "services": {
        "ei-runner": { "status": "healthy", "latency_ms": 3 }
      }
    }
  }
}
```

### 6.4 Device Registry

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/devices` | operator | List devices (paginated) |
| `POST` | `/api/v1/devices` | admin | Register a new device |
| `GET` | `/api/v1/devices/{id}` | operator | Get device by ID |
| `PATCH` | `/api/v1/devices/{id}` | admin | Update device metadata |
| `DELETE` | `/api/v1/devices/{id}` | admin | Deregister device |
| `GET` | `/api/v1/devices/{id}/key` | admin | Rotate and return new API key |

**`POST /api/v1/devices`**

```json
// Request
{
  "name": "vibration-sensor-01",
  "type": "vibration_sensor",
  "site": "factory",
  "tags": { "line": "A", "zone": "press" },
  "mqtt_topic_prefix": "factory/vibration_sensor/VS001"
}

// Response 201
{
  "id": "01958b3e-c7a0-7000-8000-aabbccddeeff",
  "name": "vibration-sensor-01",
  "type": "vibration_sensor",
  "site": "factory",
  "tags": { "line": "A", "zone": "press" },
  "mqtt_topic_prefix": "factory/vibration_sensor/VS001",
  "api_key": "p4n4_Xy7...Qz3",   // shown once — store securely
  "created_at": "2026-03-15T12:00:00Z",
  "status": "active"
}
```

**`GET /api/v1/devices?page=1&limit=20&site=factory&type=vibration_sensor`**

```json
{
  "data": [ /* Device objects without api_key */ ],
  "pagination": { "page": 1, "limit": 20, "total": 47 }
}
```

### 6.5 Telemetry

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/telemetry` | device | Ingest one or more readings |
| `GET` | `/api/v1/telemetry` | operator | Query historical data (InfluxDB proxy) |
| `GET` | `/api/v1/telemetry/stream` | operator | SSE stream of live readings |

**`POST /api/v1/telemetry`**

The API converts the JSON payload to InfluxDB line protocol and writes to
`raw_telemetry` bucket. It also publishes to the device's MQTT topic so Node-RED
flows are triggered normally.

```json
// Request (array for batch ingest)
[
  {
    "device_id": "01958b3e-c7a0-7000-8000-aabbccddeeff",
    "measurement": "celsius",
    "value": 23.7,
    "timestamp": "2026-03-15T12:00:00Z"   // optional; server time used if absent
  },
  {
    "device_id": "01958b3e-c7a0-7000-8000-aabbccddeeff",
    "measurement": "humidity",
    "value": 61.2
  }
]

// Response 202
{ "accepted": 2, "rejected": 0 }
```

**`GET /api/v1/telemetry?device_id=<id>&measurement=celsius&from=2026-03-15T00:00:00Z&to=2026-03-15T23:59:59Z&limit=1000`**

Translates query params into a Flux query and proxies the result:

```json
{
  "data": [
    {
      "device_id": "01958b3e-c7a0-7000-8000-aabbccddeeff",
      "measurement": "celsius",
      "value": 23.7,
      "timestamp": "2026-03-15T12:00:00Z"
    }
  ],
  "pagination": { "limit": 1000, "returned": 720 }
}
```

**`GET /api/v1/telemetry/stream`** — SSE

```
event: telemetry
data: {"device_id":"...","measurement":"celsius","value":24.1,"timestamp":"..."}

event: telemetry
data: {"device_id":"...","measurement":"humidity","value":60.8,"timestamp":"..."}
```

The server keeps a `tokio::sync::broadcast` channel fed by the MQTT subscription.
Each SSE connection spawns a task that receives from this channel and writes events.

### 6.6 Inference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/inference` | device/operator | Submit values for Edge Impulse inference |
| `GET` | `/api/v1/inference/results` | operator | Query recent results from InfluxDB `ai_events` |

**`POST /api/v1/inference`**

Proxies to `p4n4-edge-runner:8080` (or publishes to `sensors/raw` MQTT topic and
waits for result on `inference/results` via a one-shot channel keyed by device ID).

```json
// Request
{
  "device_id": "01958b3e-c7a0-7000-8000-aabbccddeeff",
  "values": [1.23, 4.56, 7.89, 0.12, 3.45, 6.78]
}

// Response 200
{
  "device_id": "01958b3e-c7a0-7000-8000-aabbccddeeff",
  "label": "anomaly",
  "confidence": 0.9234,
  "anomaly_score": 0.8712,
  "latency_ms": 18.4,
  "mode": "model",
  "timestamp": "2026-03-15T12:00:00Z"
}
```

### 6.7 AI Agents

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/agents` | operator | List Letta agents |
| `POST` | `/api/v1/agents/{agent_id}/chat` | operator | Send message to agent |
| `POST` | `/api/v1/agents/generate` | operator | One-shot generation via Ollama |

**`POST /api/v1/agents/{agent_id}/chat`**

Proxies to Letta REST API with auth header injected.

```json
// Request
{
  "message": "Summarise the last 10 temperature anomalies for site factory."
}

// Response 200
{
  "agent_id": "letta-agent-abc123",
  "response": "Over the past 24 hours, 10 temperature anomalies were recorded...",
  "memory_updated": true,
  "latency_ms": 342
}
```

**`POST /api/v1/agents/generate`**

One-shot prompt against Ollama (no memory/state).

```json
// Request
{
  "model": "llama3.2",    // optional, falls back to OLLAMA_DEFAULT_MODEL
  "prompt": "What does a humidity reading of 95% suggest for a server room?",
  "stream": false
}

// Response 200
{
  "model": "llama3.2",
  "response": "A humidity reading of 95% in a server room is critically high...",
  "latency_ms": 1240
}
```

### 6.8 MQTT Publish

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/mqtt/publish` | operator | Publish arbitrary message to a topic |

```json
// Request
{
  "topic": "factory/actuator/AC001/command",
  "payload": { "action": "fan_on", "speed": 80 },
  "qos": 1,      // 0|1|2, default 0
  "retain": false
}

// Response 200
{ "message_id": "uuid-...", "topic": "factory/actuator/AC001/command" }
```

---

## 7. Error Responses

All errors return a consistent JSON body with an HTTP status code:

```json
// 4xx / 5xx
{
  "error": {
    "code": "DEVICE_NOT_FOUND",
    "message": "No device found with id '01958b3e-...'",
    "request_id": "01958b3e-0000-7000-8000-000000000001"
  }
}
```

Standard error codes:

| HTTP | Code | Description |
|------|------|-------------|
| 400 | `VALIDATION_ERROR` | Request body failed validation |
| 401 | `UNAUTHORIZED` | Missing or invalid JWT |
| 403 | `FORBIDDEN` | JWT valid but role insufficient |
| 404 | `DEVICE_NOT_FOUND` | Device ID does not exist |
| 404 | `AGENT_NOT_FOUND` | Letta agent ID does not exist |
| 409 | `DEVICE_EXISTS` | Device name collision on register |
| 422 | `UPSTREAM_ERROR` | Upstream service returned unexpected response |
| 429 | `RATE_LIMITED` | Too many requests |
| 503 | `SERVICE_UNAVAILABLE` | Required upstream service unreachable |

---

## 8. Middleware Stack (Tower Layers)

Applied in order (outermost first):

```
TraceLayer          → structured request/response logs (tracing)
CompressionLayer    → gzip/br/deflate response body
CorsLayer           → configurable CORS (dev: permissive, prod: allowlist)
TimeoutLayer        → 30 s default handler timeout
PrometheusLayer     → request counter, latency histogram
RequestIdLayer      → inject X-Request-ID (UUID v7)
AuthLayer           → JWT extraction and claims injection (per-route, not global)
```

---

## 9. Database (SQLite — Device Registry)

The device registry is the **only** persistent state owned by `p4n4-api`.
SQLite is chosen to keep the deployment self-contained (no extra service, file
lives in a Docker volume).

### Schema

```sql
-- migrations/0001_create_devices.sql

CREATE TABLE devices (
    id             TEXT PRIMARY KEY,          -- UUID v7
    name           TEXT NOT NULL UNIQUE,
    device_type    TEXT NOT NULL,
    site           TEXT NOT NULL,
    tags           TEXT NOT NULL DEFAULT '{}', -- JSON object
    topic_prefix   TEXT NOT NULL,
    api_key_hash   TEXT NOT NULL,              -- argon2id of raw key
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TEXT NOT NULL,              -- RFC 3339
    updated_at     TEXT NOT NULL
);

CREATE INDEX idx_devices_site ON devices(site);
CREATE INDEX idx_devices_type ON devices(device_type);
CREATE INDEX idx_devices_status ON devices(status);
```

### Key design decisions

- `api_key_hash` stores argon2id hash; the plaintext is returned **once** at registration.
- Auth at `POST /api/v1/auth/token` verifies the raw key against the hash.
- `sqlx`'s compile-time query checking (`query!` macro) is used throughout `db/`.

---

## 10. Docker & Deployment

### Dockerfile

Multi-stage build: `chef` plan → `builder` compile → minimal `runtime` image.

```dockerfile
# Stage 1: dependency planner
FROM rust:1.80-slim AS chef
RUN cargo install cargo-chef
WORKDIR /app
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

# Stage 2: dependency cache
FROM rust:1.80-slim AS deps
RUN cargo install cargo-chef
WORKDIR /app
COPY --from=chef /app/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json

# Stage 3: build
FROM rust:1.80-slim AS builder
WORKDIR /app
COPY --from=deps /app .
COPY . .
RUN cargo build --release

# Stage 4: minimal runtime
FROM gcr.io/distroless/cc-debian12 AS runtime
WORKDIR /app
COPY --from=builder /app/target/release/p4n4-api .
EXPOSE 8000
ENTRYPOINT ["/app/p4n4-api"]
```

### docker-compose.yml

```yaml
services:
  p4n4-api:
    build: .
    image: ghcr.io/raisga/p4n4-api:latest
    container_name: p4n4-api
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - p4n4-api-data:/data
    env_file:
      - .env
    depends_on:
      - p4n4-influxdb   # from p4n4-iot
      - p4n4-mqtt       # from p4n4-iot
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks:
      - p4n4-net

volumes:
  p4n4-api-data:

networks:
  p4n4-net:
    external: true
```

**Port:** `8000` (matches platform convention; does not conflict with any existing service)

---

## 11. Authentication Flow

```
Client                   p4n4-api                   DB
  │                          │                        │
  │  POST /auth/token        │                        │
  │  { api_key: "p4n4_..." } │                        │
  │─────────────────────────▶│                        │
  │                          │  SELECT * FROM devices │
  │                          │  WHERE name = ?        │
  │                          │───────────────────────▶│
  │                          │◀───────────────────────│
  │                          │  argon2id verify(key)  │
  │                          │  sign JWT(sub, role)   │
  │◀─────────────────────────│                        │
  │  { access_token, ... }   │                        │
  │                          │                        │
  │  GET /api/v1/telemetry   │                        │
  │  Authorization: Bearer   │                        │
  │─────────────────────────▶│                        │
  │                          │  validate JWT          │
  │                          │  extract Claims        │
  │                          │  check role >= operator│
  │                          │  proxy → InfluxDB      │
  │◀─────────────────────────│                        │
  │  { data: [...] }         │                        │
```

---

## 12. Observability

### Logs (tracing + tracing-subscriber)

Structured JSON log output:

```json
{
  "timestamp": "2026-03-15T12:00:00.123Z",
  "level": "INFO",
  "target": "p4n4_api::routes::telemetry",
  "message": "ingest accepted",
  "request_id": "01958b3e-...",
  "device_id": "01958b3e-...",
  "accepted": 2,
  "rejected": 0,
  "duration_ms": 12
}
```

Set `P4N4_API_LOG_LEVEL=debug` to enable per-request upstream call logs.

### Metrics (`/metrics`)

Key Prometheus metrics exposed:

| Metric | Type | Labels |
|--------|------|--------|
| `p4n4_api_requests_total` | Counter | method, path, status |
| `p4n4_api_request_duration_seconds` | Histogram | method, path |
| `p4n4_api_upstream_calls_total` | Counter | service, status |
| `p4n4_api_upstream_latency_seconds` | Histogram | service |
| `p4n4_api_telemetry_points_ingested_total` | Counter | device_type |
| `p4n4_api_active_sse_connections` | Gauge | — |
| `p4n4_api_mqtt_messages_published_total` | Counter | — |

Grafana datasource can be pointed at `p4n4-api:8000/metrics` for API-specific dashboards.

---

## 13. CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`):

```
on: [push, pull_request]

jobs:
  check:
    - cargo fmt --check
    - cargo clippy -- -D warnings
    - cargo test
    - cargo build --release

  docker:
    needs: check
    on: push to main
    - docker buildx build
    - push to ghcr.io/raisga/p4n4-api

  release:
    on: tag v*.*.*
    - Create GitHub Release
    - Attach static binary (musl)
```

---

## 14. Implementation Milestones

| Milestone | Scope |
|-----------|-------|
| **M1 — Skeleton** | Cargo.toml, Axum router, `/health`, `/ready`, `/openapi.json`, Dockerfile, `docker-compose.yml`, CI |
| **M2 — Auth** | Device registry (SQLite + sqlx), API key hashing, JWT issue/refresh, `RequireAuth` extractor |
| **M3 — Telemetry** | InfluxDB client, `POST /telemetry`, `GET /telemetry`, MQTT publish on ingest |
| **M4 — SSE** | MQTT subscriber task, broadcast channel, `GET /telemetry/stream` |
| **M5 — Stacks** | Health check probes for each service, `GET /stacks` |
| **M6 — Inference** | Edge Runner client, `POST /inference`, `GET /inference/results` |
| **M7 — Agents** | Ollama client, Letta client, `/agents/generate`, `/agents/{id}/chat` |
| **M8 — MQTT Publish** | `POST /mqtt/publish` with QoS + retain support |
| **M9 — Hardening** | Rate limiting, metrics, request ID propagation, integration tests |
| **M10 — Docs** | Swagger UI polish, DESIGN.md → mkdocs page in p4n4-docs |

---

## 15. Open Questions

1. **API key storage location** — SQLite is simplest, but should the device registry
   eventually live in InfluxDB (as a `devices` measurement) for uniformity?
   *Recommendation: Keep SQLite for relational device metadata; InfluxDB is
   purpose-built for append-only time-series, not keyed lookups.*

2. **Rate limiting strategy** — per-IP vs. per-device-ID?
   *Recommendation: Per `sub` claim (device/user ID) using a token bucket in memory
   (no Redis dependency). Evict stale buckets with a background task.*

3. **WebSocket vs SSE for real-time** — SSE is simpler (unidirectional, HTTP/1.1
   compatible, auto-reconnect). WebSocket adds bidirectional capability.
   *Recommendation: Start with SSE; upgrade to WebSocket only if bidirectional
   command-and-control use cases emerge.*

4. **n8n integration** — n8n is not exposed through the API today. Should there be
   a `POST /api/v1/workflows/{id}/trigger` endpoint?
   *Recommendation: Defer. n8n webhooks are already reachable at port 5678.
   Add a thin proxy in M9 if the CLI needs to trigger workflows.*

5. **Multi-tenancy** — The current design is single-project (one InfluxDB org).
   Should `site` be the tenant boundary?
   *Recommendation: Yes, use `site` as the InfluxDB tag filter for all queries.
   Full multi-tenancy (multiple orgs/tokens) is a v2 concern.*
