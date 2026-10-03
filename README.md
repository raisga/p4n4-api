# p4n4-api

> Unified **REST API gateway** for the P4N4 platform — written in Python.

`p4n4-api` is the central HTTP API layer for the P4N4 IoT + GenAI + Edge AI platform. It exposes a single, versioned, authenticated REST surface over the otherwise fragmented set of internal services (InfluxDB, MQTT, Ollama, Letta, Edge Impulse Runner), making it easy to build dashboards, mobile apps, or external integrations without touching each service directly.

Part of the [p4n4](https://github.com/raisga/p4n4) platform — an EdgeAI + GenAI integration platform for IoT deployments.

---

## Status

**v0.1** implements user sign-in (JWT) and a read-only project/stack/edge-metrics surface
built on [`p4n4-lib`](https://github.com/raisga/p4n4-lib) (manifest, layout, validation, and
Compose status — both flat and multi-layer project layouts). Endpoints marked 🔒 need an
`operator` or `admin` access token; 🔑 needs `admin`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe: project found and Docker reachable (`503` otherwise) |
| `GET` | `/api/v1/version` | API, API-version and `p4n4-lib` versions |
| `POST` | `/api/v1/auth/token` | Username + password → access token (1 h) + refresh token (7 d); or a device's `api_key` → access token (1 h) |
| `POST` | `/api/v1/auth/refresh` | Refresh token → new pair (each refresh token works once) |
| `POST` | `/api/v1/auth/logout` | Revoke the refresh token and its whole sign-in, access tokens included |
| `POST` | `/api/v1/auth/password` | Change your own password (🔒 any role): signs out every other sign-in and returns a new pair |
| `GET` | `/api/v1/auth/me` | The signed-in user and role (🔒 any role) |
| `GET` `POST` | `/api/v1/users` | 🔑 List users / create one (`{username, password, role}`, role defaults to `operator`) |
| `GET` `PATCH` `DELETE` | `/api/v1/users/{username}` | 🔑 Read, change (`{role?, password?}`) or remove a user. The last admin can't be demoted or removed (`409`) |
| `GET` | `/api/v1/devices` | 🔒 Device registry, paginated (`limit`, `offset`) |
| `POST` | `/api/v1/devices` | 🔑 Register a device; the response holds its API key, shown once |
| `GET` `PATCH` `DELETE` | `/api/v1/devices/{id}` | 🔒 Read; 🔑 change (`name`, `description`, `enabled`) or remove |
| `POST` | `/api/v1/devices/{id}/key` | 🔑 Rotate the API key (old key and its tokens stop at once) |
| `GET` | `/api/v1/project` | 🔒 Manifest (including its optional `template` and `dashboard` blocks), layout (`flat`/`multi`), and per-stack directories |
| `GET` | `/api/v1/project/validate` | 🔒 Run `p4n4_lib.validate` checks; returns `{ok, passed, errors}` |
| `GET` | `/api/v1/stacks` | 🔒 Compose service status per stack (`503` if Docker is unreachable) |
| `GET` | `/api/v1/stacks/{stack}` | 🔒 One stack's service status (404 if not enabled) |
| `POST` | `/api/v1/stacks/{stack\|all}/{up\|down\|restart}` | 🔑 Run a stack action as a background job (`202` + `Location`) |
| `POST` | `/api/v1/stacks/{stack}/services/{service}/restart` | 🔑 Restart one service, as a job |
| `GET` | `/api/v1/stacks/{stack}/logs` | 🔑 Container logs (`tail`, `service`); `follow=true` streams them as server-sent events |
| `GET` | `/api/v1/jobs`, `/api/v1/jobs/{id}` | 🔒 Job status and Compose output |
| `GET` | `/api/v1/audit` | 🔑 Audit log: stack actions, user and device changes |
| `GET` | `/api/v1/edge/metrics` | 🔒 CPU, memory, disk, temperature, uptime and load of the host (the edge device) |
| `GET` | `/swagger-ui`, `/openapi.json` | Interactive docs / OpenAPI spec |

Everything else in this README (telemetry and its live stream, inference, agents, MQTT,
Prometheus metrics) is the **design target**, not yet implemented. See [TODO.md](TODO.md) for the plan.

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

3. **Create the first admin** (prompts for a password, at least 10 characters)

   ```bash
   uv run p4n4-api users add admin --role admin
   # Or generate a password, shown once (does nothing if any user exists, so safe in scripts):
   uv run p4n4-api users bootstrap
   ```

4. **Start the API**

   ```bash
   uv run p4n4-api
   # or: uv run uvicorn p4n4_api.main:app --reload --port 8000
   ```

5. **Verify it is running**

   ```bash
   curl http://localhost:8000/health
   # {"status":"ok"}

   TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/token \
     -H 'Content-Type: application/json' \
     -d '{"username": "admin", "password": "<password>"}' | jq -r .access_token)

   curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/project
   curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/stacks

   # Open the interactive API docs (paste the token under "Authorize")
   open http://localhost:8000/swagger-ui
   ```

### Users

```bash
p4n4-api users list
p4n4-api users bootstrap                 # first admin with a generated password; no-op once users exist
p4n4-api users add alice                 # operator by default; --role admin
p4n4-api users passwd alice              # also signs alice out everywhere
p4n4-api users role alice admin
p4n4-api users remove alice
# Scripts: echo "$PASSWORD" | p4n4-api users add alice --password-stdin
```

Admins can do the same over HTTP (`/api/v1/users`), e.g. from the dashboard, and anyone signed
in can change their own password (`POST /api/v1/auth/password`). The API won't demote or remove
the last admin; the CLI will, so it stays the way back in.

`operator` can read project, stack and edge status, the device registry and job progress;
`admin` can also manage users and devices, start/stop/restart stacks, read container logs
and read the audit log. A user's role is read on every
request, so role changes and removals apply immediately. Changing or resetting a password
signs that user out everywhere.

---

## Configuration

All configuration is read from environment variables (via `pydantic-settings`; empty values count as unset, invalid ones stop startup with the variable named). Currently used:

| Variable | Description |
|---|---|
| `P4N4_PROJECT_DIR` | p4n4 project directory to serve (walks up to `.p4n4.json`; default: the server's cwd) |
| `P4N4_API_HOST` | Bind address (default: `127.0.0.1`) |
| `P4N4_API_PORT` | HTTP listen port (default: `8000`) |
| `P4N4_API_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API, e.g. `http://localhost:8088`. Empty (default) disables CORS. No credentials are allowed, so `*` is accepted |
| `P4N4_API_DATA_DIR` | Where the API keeps its SQLite database (`api.db`: users, refresh tokens) and generated JWT secret (default: `$XDG_DATA_HOME/p4n4-api`, i.e. `~/.local/share/p4n4-api`). Back it up; created owner-only |
| `P4N4_API_JWT_SECRET` | HS256 signing key, at least 32 characters (`openssl rand -hex 32`). Default: generated once into `$P4N4_API_DATA_DIR/jwt_secret`. Changing it signs everyone out |
| `P4N4_API_TRUSTED_PROXIES` | Comma-separated reverse-proxy IPs or networks whose `X-Forwarded-For` is believed, so the sign-in rate limit applies per client instead of to everyone behind the proxy. For the dashboard's nginx container reaching a host-run API: `172.16.0.0/12` (Docker's default bridge networks). Default: none; the header is ignored |
| `P4N4_API_LOG_FORMAT` | `text` (default) or `json` (one object per line, for log collectors). Applies to `p4n4-api serve` |
| `P4N4_API_LOG_LEVEL` | `debug`, `info` (default), `warning` or `error` |
| `P4N4_API_AUTH` | `off` disables auth: every request is treated as an admin. **Development only**; logs a warning at startup |

Planned (for the upstream-proxy features below): `INFLUXDB_URL`,
`INFLUXDB_TOKEN`, `MQTT_HOST`, `MQTT_USER`/`MQTT_PASSWORD`, `OLLAMA_URL`, `LETTA_URL`,
`LETTA_SERVER_PASSWORD`, `EDGE_RUNNER_URL`.

---

## API Reference

Base path: `/api/v1`
Authentication: `Authorization: Bearer <jwt>` (except public endpoints)

### Errors

Every error (4xx/5xx) has the same body:

```json
{"error": {"code": "not_found", "message": "Stack 'nope' not found in this project."}}
```

Every response, errors included, carries an `X-Request-ID` header: the caller's own (up to 64 of `A-Z a-z 0-9 . _ : -`) or a generated one. Log lines written while handling the request carry the same ID, so quote it when reporting a problem.

`code` is stable and meant for programs: by default it follows the status (`bad_request`, `unauthorized`, `forbidden`, `not_found`, `method_not_allowed`, `conflict`, `validation_error`, `rate_limited`, `internal_error`, `unavailable`), with specific codes where one status means different things (`409`: `user_exists`, `last_admin`). `message` is for people and may change. `validation_error` from request parsing adds `fields: [{loc, message}]`. Unexpected failures return `500 internal_error` without details; the server log has the traceback. `GET /ready`'s `503` is a status report (`{status, checks}`), not an error.

### Public endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/ready` | Readiness probe (project + Docker today; DB + MQTT once they exist) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/openapi.json` | OpenAPI 3.1 spec |
| `GET` | `/swagger-ui` | Swagger UI |

### Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/token` | People: `{username, password}` → access + refresh token. Devices: `{api_key}` → access token only (exchange the key again when it expires) |
| `POST` | `/api/v1/auth/refresh` | Exchange a refresh token for a new pair (single use) |
| `POST` | `/api/v1/auth/logout` | Revoke a refresh token and every token from the same sign-in |
| `POST` | `/api/v1/auth/password` | Change your own password (`{current_password, new_password}`); returns a new pair |
| `GET` | `/api/v1/auth/me` | Current user and role |

### Users (admin)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/users` | List users (`username`, `role`, `created_at`) |
| `POST` | `/api/v1/users` | Create a user (`201`; `409` if the name is taken, `422` if invalid) |
| `GET` | `/api/v1/users/{username}` | One user |
| `PATCH` | `/api/v1/users/{username}` | Change the role and/or reset the password; both apply or neither does |
| `DELETE` | `/api/v1/users/{username}` | Remove a user (`204`) |

Demoting or removing the last admin returns `409`.

### Stack health (operator+)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/stacks` | Health of all platform stacks |
| `GET` | `/api/v1/stacks/{stack}` | Health of one stack (`iot`, `ai`, `edge`) |

Each service reports `name`, `state`, `health`, plus `image`, `version` (the image tag),
`status` (Docker's text, e.g. `Up 2 hours (healthy)`), `exit_code` (stopped services only),
`ports` (published), `started_at` and `uptime_s` (running services only). Fields Docker
doesn't provide (e.g. with standalone `docker-compose` v1) are `null`.

### Stack control (admin)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/stacks/{stack}/{action}` | `up`, `down` or `restart` a stack, or `all` of them (up in dependency order iot → ai → edge → dashboard, down in reverse; stops at the first failure). `?pull=true` (up only) pulls newer images first. `down` never removes volumes |
| `POST` | `/api/v1/stacks/{stack}/services/{service}/restart` | Restart one service (`404` unless the stack defines it) |
| `GET` | `/api/v1/stacks/{stack}/logs` | `?tail=` (1–5000, default 200) `&service=`; JSON `{stack, service, lines}`. With `&follow=true`: `text/event-stream`, one `data:` per line, a keep-alive comment every 15 s, and `event: end` with Compose's exit code. Compose is stopped when the client disconnects |

Actions answer `202` with a job and a `Location: /api/v1/jobs/{id}` header; poll it until
`status` is `succeeded` or `failed`. Jobs run one at a time in the order queued, so Compose
operations never overlap; queuing the same action again while it's still waiting returns
the waiting job. A job is stopped after 30 minutes. Jobs are kept in memory (the newest
100, each with the last 500 lines of output), so restarting the API forgets them; the audit
log keeps who ran what. Logs are admin-only because they can contain secrets.

### Jobs (operator+)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/jobs` | Recent jobs, newest first (`?limit=`, without output) |
| `GET` | `/api/v1/jobs/{id}` | One job: `status` (`queued`, `running`, `succeeded`, `failed`), timestamps, `exit_code`, `output` |

### Audit log (admin)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/audit` | Newest first, `?limit=` (1–500) `&offset=`. Entries: `at`, `actor`, `action` (`stack.up`, `user.create`, `device.rotate_key`, …), `target`, `outcome`, `request_id` |

Stack actions are logged when queued and when finished (with the exit code). User and
device changes are logged in the same transaction as the change, so a refused change leaves
no entry.

### Devices (admin for writes, operator for reads)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/devices` | List devices by ID: `?limit=` (1–200, default 50) `&offset=`; returns `{items, total, limit, offset}` |
| `POST` | `/api/v1/devices` | Register a device (`{id, name?, description?}`); `201` with its `api_key`, shown once. `409 device_exists` if the ID is taken |
| `GET` | `/api/v1/devices/{id}` | One device: `id`, `name`, `description`, `enabled`, `key_prefix`, `created_at`, `last_seen_at` |
| `PATCH` | `/api/v1/devices/{id}` | Change `name`, `description` or `enabled` |
| `DELETE` | `/api/v1/devices/{id}` | Deregister; its key and tokens stop working at once |
| `POST` | `/api/v1/devices/{id}/key` | Rotate the API key; the new one is shown once, the old one and its tokens stop at once |

Device IDs are lowercase slugs (`greenhouse-01`), since they'll tag telemetry. API keys look
like `p4n4_<key id>_<secret>`; `key_prefix` (`p4n4_<key id>`) identifies one without
revealing it. A device signs in with `POST /api/v1/auth/token {"api_key": ...}` and sends the
access token as `Authorization: Bearer`, like people do; its role, `device`, grants telemetry
ingest (M5) and nothing operators can do. Disabling a device is a pause: re-enabling makes its
current key and tokens valid again. For a lost or leaked key, rotate it or delete the device.

### Telemetry

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/telemetry` | device | Ingest readings (→ InfluxDB + MQTT) |
| `GET` | `/api/v1/telemetry` | operator | Query historical data |
| `GET` | `/api/v1/telemetry/stream` | operator | SSE live stream |

### Edge

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/edge/metrics` | Host system metrics, in the [dashboard's edge metrics contract](https://github.com/raisga/p4n4-dashboard#edge-metrics-contract) |

Fields: `cpu_percent`, `mem_percent`, `mem_used_mb`, `mem_total_mb`, `disk_percent` (of `/`),
`uptime_s`, `load` (1/5/15 min) and `temp_c`. `temp_c` comes from a CPU/SoC sensor
(`cpu_thermal` on a Raspberry Pi, `coretemp`/`k10temp` on x86) and is omitted when none is
found, e.g. on macOS, Windows or in a VM. `inference_ms` is added once the Edge Impulse runner
proxy exists.

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

Request and response schemas for implemented endpoints are in the OpenAPI spec (`/openapi.json`, browsable at `/swagger-ui`); the build order is in [TODO.md](TODO.md).

---

## Project Structure

Current (v0.1):

```
p4n4-api/
├── pyproject.toml
├── p4n4_api/
│   ├── main.py              # FastAPI app factory, startup checks, role guards
│   ├── cli.py               # `p4n4-api` command: serve (default) and `users`
│   ├── config.py            # Settings from environment variables
│   ├── deps.py              # Project resolution dependency (p4n4_lib.manifest)
│   ├── db.py                # SQLite database and schema migrations
│   ├── users.py             # Accounts, roles, argon2id password hashing
│   ├── devices.py           # Device registry and API keys
│   ├── auth.py              # JWT issue/verify, refresh rotation, role dependencies, rate limit
│   ├── docker.py            # Docker daemon check, container start times
│   ├── errors.py            # Standard error body and exception handlers
│   ├── logs.py              # Request IDs, access log, text/JSON log formatting
│   ├── jobs.py              # Background stack actions (one at a time, in memory)
│   ├── audit.py             # Append-only audit log
│   └── routes/              # One APIRouter per API group
│       ├── auth.py          # /api/v1/auth/token, /refresh, /logout, /password, /me
│       ├── users.py         # /api/v1/users (admin user management)
│       ├── devices.py       # /api/v1/devices (registry, key rotation)
│       ├── health.py        # GET /health, /ready, /api/v1/version
│       ├── project.py       # GET /api/v1/project, /project/validate
│       ├── stacks.py        # GET /api/v1/stacks, /stacks/{stack}
│       ├── control.py       # Stack up/down/restart, service restart, logs (SSE)
│       ├── jobs.py          # GET /api/v1/jobs
│       ├── audit.py         # GET /api/v1/audit
│       └── edge.py          # GET /api/v1/edge/metrics
└── tests/
```

Planned additions as the upstream-proxy features land: `clients/`
(async HTTP/MQTT clients per upstream service), `models/` (Pydantic schemas),
`Dockerfile` + `docker-compose.yml`.

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

- **JWT** — HS256 signed tokens. Access tokens expire in 1 hour; refresh tokens in 7 days. Roles are `operator` and `admin` for people (ranked), and `device` (separate: devices can only do device things). The role is read from the database on every request, not trusted from the token; device tokens are re-checked too, so rotating a key, disabling or deleting a device ends its tokens at once.
- **Refresh rotation** — each refresh token works once. Reusing one revokes every token from that sign-in. Password changes sign the user out everywhere. Signing out revokes that sign-in's refresh **and** access tokens at once (access tokens carry their sign-in's ID, `sid`, checked on every request); other sign-ins are untouched.
- **Passwords** — argon2id, at least 10 characters. Unknown usernames take as long to reject as wrong passwords.
- **API keys** — 256-bit random secrets, stored as argon2id hashes; plaintext shown only once, at registration and at rotation. Unknown keys take as long to reject as wrong ones. Key exchange shares the sign-in rate limit.
- **Secrets** — never stored in the database; all upstream credentials are injected via environment variables.
- **Rate limiting** — `/auth/token`, `/auth/refresh` and `/auth/password`: 10 attempts per client address, then one every 6 s (in-memory token bucket; `429` + `Retry-After`). Behind a reverse proxy, set `P4N4_API_TRUSTED_PROXIES` or every client shares the proxy's limit; the client is the rightmost `X-Forwarded-For` entry that isn't a trusted proxy. `p4n4-api` turns off uvicorn's own proxy-header handling (which trusts `127.0.0.1`); when running `uvicorn` directly, pass `--no-proxy-headers` for the same behaviour. There's deliberately no per-username limit: it would let anyone lock a known user out.
- **CORS** — off by default; allowlist via `P4N4_API_CORS_ORIGINS`. Credentials (cookies) are never allowed: auth uses the `Authorization` header.
- **Port exposure** — for production, remove the `8000` host-port binding and front with a reverse proxy (nginx, Caddy, Traefik).

---

## Resources

- [p4n4 Platform](https://github.com/raisga/p4n4) — umbrella repo and architecture docs
- [TODO.md](TODO.md) — status, milestones and open questions
- [p4n4-lib](https://github.com/raisga/p4n4-lib) — shared library (manifest, layout, validation, Compose wrappers) consumed by this package
- [p4n4-iot](https://github.com/raisga/p4n4-iot) — IoT stack (MQTT, InfluxDB, Node-RED, Grafana)
- [p4n4-ai](https://github.com/raisga/p4n4-ai) — GenAI stack (Ollama, Letta, n8n)
- [p4n4-edge](https://github.com/raisga/p4n4-edge) — Edge AI stack (Edge Impulse runner)
- [FastAPI](https://fastapi.tiangolo.com/) — Python web framework (OpenAPI 3.1 built-in)
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — settings management via environment variables

---

## License

This project is licensed under the [MIT License](LICENSE).
