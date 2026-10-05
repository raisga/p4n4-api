# p4n4-api

> Unified **REST API gateway** for the P4N4 platform — written in Python.

`p4n4-api` is the central HTTP API layer for the P4N4 IoT + GenAI + Edge AI platform. It exposes a single, versioned, authenticated REST surface over the otherwise fragmented set of internal services (InfluxDB, MQTT, Ollama, Letta, Edge Impulse Runner), making it easy to build dashboards, mobile apps, or external integrations without touching each service directly.

Part of the [p4n4](https://github.com/raisga/p4n4) platform — an EdgeAI + GenAI integration platform for IoT deployments.

---

## Status

**v0.1** implements user sign-in (JWT) and a read-only project/stack/edge-metrics surface
built on [`p4n4-lib`](https://github.com/raisga/p4n4-lib) (manifest, layout, validation, and
Compose status — both flat and multi-layer project layouts). Endpoints marked 🔒 need a
`normie`, `operator` or `admin` access token; 🔑 needs `admin`:

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
| `POST` | `/api/v1/telemetry` | Device token only: store a batch of readings in InfluxDB, then publish them to MQTT |
| `GET` | `/api/v1/telemetry` | 🔒 Stored readings (filters, time range, windowed aggregates) |
| `GET` | `/api/v1/telemetry/stream` | 🔒 Live readings from MQTT as server-sent events |
| `GET` | `/api/v1/inference/runner` | 🔒 The edge runner's backend (Edge Impulse, ONNX or mock), model and counters |
| `POST` | `/api/v1/inference` | 🔒 Classify a feature vector with the runner's model |
| `GET` | `/api/v1/inference/results` | 🔒 Results the runner's pipeline stored (`ai_events`) |
| `GET` | `/api/v1/dashboard/views` | 🔒 Tabs each p4n4-dashboard view shows and their order: `{tab_order, power_tabs, normie_tabs}` (null = the dashboard's default) |
| `PUT` | `/api/v1/dashboard/views` | 🔑 Set them for every device (audited) |
| `GET` | `/api/v1/agents/config` | 🔒 The assistant everyone chats with: `{backend, model, agent_id}` (null = the first one listed), and `updated_at`/`updated_by` (null until someone chooses; p4n4-dashboard then offers its brand's default) |
| `PUT` | `/api/v1/agents/config` | 🔒 `operator` or `admin`: choose the assistant (audited) |
| `GET` | `/api/v1/agents/models` | 🔒 Ollama models |
| `POST` | `/api/v1/agents/chat` | 🔒 Ollama chat, streamed as Ollama's NDJSON. Normies: the assistant's model only, no `options` (`403 assistant_restricted`) |
| `POST` | `/api/v1/agents/generate` | 🔒 `operator` or `admin`: Ollama one-shot generation |
| `GET` | `/api/v1/agents` | 🔒 Letta agents |
| `POST` | `/api/v1/agents/{id}/chat` | 🔒 Message a Letta agent (password kept server side). Normies: the assistant's agent only |
| `POST` | `/api/v1/mqtt/publish` | 🔒 Publish an MQTT message (allowed topics only) |
| `GET` | `/api/v1/edge/metrics` | 🔒 CPU, memory, disk, temperature, uptime and load of the host (the edge device) |
| `GET` | `/swagger-ui`, `/openapi.json` | Interactive docs / OpenAPI spec |

Everything else in this README (Prometheus metrics) is the **design target**, not yet
implemented. See [TODO.md](TODO.md) for the plan.

---

## Table of Contents

- [Architecture](#architecture)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Running in Docker](#running-in-docker)
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

- **Unified auth** — API key → JWT (HS256). Four roles: `device`, `normie`, `operator`, `admin`.
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
   uv pip install -e .                        # p4n4-lib from PyPI
   # lib main: uv pip install "p4n4-lib @ git+https://github.com/raisga/p4n4-lib.git" -e .
   # monorepo: uv pip install -e ../lib -e .
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
p4n4-api users dev                       # admin, power, normie (see Dev users)
p4n4-api users add alice                 # operator by default; --role normie|admin
p4n4-api users passwd alice              # also signs alice out everywhere
p4n4-api users role alice admin
p4n4-api users remove alice
# Scripts: echo "$PASSWORD" | p4n4-api users add alice --password-stdin
```

Admins can do the same over HTTP (`/api/v1/users`), e.g. from the dashboard, and anyone signed
in can change their own password (`POST /api/v1/auth/password`). The API won't demote or remove
the last admin; the CLI will, so it stays the way back in.

`normie` can read project, stack and edge status, telemetry, inference results and job
progress, and chat with Ollama models and Letta agents (the dashboard's simplified view);
`operator` can also read the device registry, publish MQTT messages, run inference and
one-shot generation; `admin` can also manage users and devices, start/stop/restart stacks, read container logs
and read the audit log. A user's role is read on every
request, so role changes and removals apply immediately. Changing or resetting a password
signs that user out everywhere.

#### Dev users

For development, one account per dashboard view, all with the password `p4n4`:

| Username | Role | Dashboard view |
|---|---|---|
| `admin` | `admin` | admin |
| `power` | `operator` | power |
| `normie` | `normie` | normie |

Create them on every start with `P4N4_API_DEV_USERS=true`, or once with `p4n4-api users dev`.
Either way only missing accounts are created; ones that exist keep their role and password
(so an `admin` from `users bootstrap` keeps its generated password). The password is public
and shorter than real ones may be, so **never** set this on a deployment others can reach.
To clean up, remove `power` and `normie` (and `admin`, once another admin exists) with
`p4n4-api users remove`, and unset the variable.

---

## Running in Docker

The image (`ghcr.io/raisga/p4n4-api`, amd64 and arm64) runs as a non-root user with a
read-only root filesystem, and `docker-compose.yml` attaches it to `p4n4-net` as
`p4n4-api`, so the dashboard and the stacks' containers reach it at `http://p4n4-api:8000`.
Its upstream URLs default to the stacks' containers (`p4n4-influxdb`, `p4n4-mqtt`,
`p4n4-ei-runner`, `p4n4-ollama`, `p4n4-letta`), not the host's published ports.

```bash
cp .env.example .env          # set P4N4_PROJECT_DIR (absolute path)
docker compose up -d
docker compose exec api p4n4-api users bootstrap    # first admin; password shown once
```

- **Project:** mounted read-only at the **same path** as on the host. Compose sends the
  stacks' relative bind mounts (`./config`, `./data`) to the daemon as host paths, so a
  different path inside the container would mount the wrong (empty) directories on `up`.
  InfluxDB, MQTT and Letta credentials are read from the project's `.env` files as on the host.
- **Data:** the `p4n4-api-data` volume (`/data`): users, devices, audit log and the generated
  JWT secret. Back it up. A host-run API's data dir isn't carried over; copy its files into
  the volume, or start fresh with `users bootstrap`.
- **Docker access is off by default** (`P4N4_API_DOCKER=off`): `/stacks` returns `503`, so the
  dashboard falls back to port probes, and stack control and logs are unavailable. Turning it
  on mounts the Docker socket, which gives the container **root-equivalent control of the
  host**: only do it where anyone who gets an admin token may also have that.

  ```bash
  echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> .env
  docker compose -f docker-compose.yml -f docker-compose.docker.yml up -d
  ```

  The image includes the Docker CLI and Compose plugin; nothing else on the host is needed.
  A socket proxy that allows only the calls in use would narrow this; it's not done yet.
- **Host metrics:** `/edge/metrics` reads `/proc` and `/sys`, which show the host's CPU,
  memory, load, uptime and temperatures without extra mounts. `disk_percent` is for the
  container's `/`, which lives on Docker's data root (usually the host's root filesystem).
  For another disk, mount a directory on it read-only and point `P4N4_API_DISK_PATH` at it.
- **Ports:** published on `127.0.0.1:8000` only (`P4N4_API_BIND`, `P4N4_API_PUBLISH_PORT`).
  The dashboard doesn't need it: set its `P4N4_API_UPSTREAM=http://p4n4-api:8000`.
- **Proxies:** `P4N4_API_TRUSTED_PROXIES` defaults to `172.16.0.0/12` here, so the sign-in
  rate limit is per client behind the dashboard's nginx.

Build it yourself with `docker build -t ghcr.io/raisga/p4n4-api:dev .` and
`P4N4_API_VERSION=dev`. The image installs `p4n4-lib` from PyPI; `--build-arg P4N4_LIB=...`
picks another one (any pip requirement, e.g. a git tag).

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
| `P4N4_API_INFLUXDB_URL` | InfluxDB 2 base URL (default `http://localhost:8086`, the IoT stack's published port) |
| `P4N4_API_INFLUXDB_TOKEN`, `_ORG`, `_BUCKET` | Default to `INFLUXDB_TOKEN`, `INFLUXDB_ORG` and `INFLUXDB_BUCKET` from the project's `iot/.env` (the stack's own), then `ming` / `raw_telemetry`. Without a token, telemetry endpoints return `503 influxdb_not_configured` |
| `P4N4_API_MQTT_ENABLED` | `false` turns the MQTT connection off (no publishing, no live stream). Default `true` |
| `P4N4_API_MQTT_HOST`, `_PORT` | Broker (default `localhost:1883`, the IoT stack's) |
| `P4N4_API_MQTT_USERNAME`, `_PASSWORD` | Broker credentials, if it requires them |
| `P4N4_API_EDGE_RUNNER_URL` | The edge stack's inference runner (default `http://localhost:8080`, its published port) |
| `P4N4_API_MQTT_PUBLISH_ALLOW` | Topic filters `POST /mqtt/publish` may use, comma-separated (`+`, `#` wildcards). Default `#` |
| `P4N4_API_MQTT_PUBLISH_DENY` | Topic filters it may not use; wins over allow. Default `sensors/#,inference/#` (device data, which Node-RED stores). `none` for no denials |
| `P4N4_API_OLLAMA_URL` | Ollama (default `http://localhost:11434`, the ai stack's published port) |
| `P4N4_API_LETTA_URL` | Letta (default `http://localhost:8283`) |
| `P4N4_API_LETTA_PASSWORD` | Letta's server password; default `LETTA_SERVER_PASSWORD` from the project's `ai/.env`. Sent only from the API to Letta |
| `P4N4_API_INFLUXDB_AI_EVENTS_BUCKET` | Where the runner stores results; default `INFLUXDB_BUCKET_AI_EVENTS` from the project's `edge/.env`, then `ai_events` |
| `P4N4_API_DOCKER` | `off` when the API has no Docker access (a container without the socket): stack status, control and logs return `503`, and `/ready` doesn't require Docker. Default `on` |
| `P4N4_API_DISK_PATH` | Filesystem `/edge/metrics` reports as `disk_percent` (default `/`) |
| `P4N4_API_AUTH` | `off` disables auth: every request is treated as an admin. **Development only**; logs a warning at startup |
| `P4N4_API_DEV_USERS` | `true` creates `admin`, `power` and `normie` (password `p4n4`) at startup if missing; see [Dev users](#dev-users). **Development only**; logs a warning at startup |

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

### Stack health (normie+)

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

### Jobs (normie+)

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
| `POST` | `/api/v1/telemetry` | device | Store a batch of readings, then publish each to MQTT |
| `GET` | `/api/v1/telemetry` | normie+ | Stored readings |
| `GET` | `/api/v1/telemetry/stream` | normie+ | Live readings (server-sent events) |

**Ingest.** A device sends readings for itself (the device comes from its token):

```json
{"readings": [{"sensor": "temperature", "fields": {"value": 21.5, "unit": "C"}, "ts": "2026-10-02T12:00:00Z"}]}
```

1–1000 readings per batch. `sensor` is a slug (letters, digits, `.`, `_`, `-`); `fields` are
numbers, strings (≤ 1024 characters) or booleans, named anything but `device`, `model`, `ts`
or `_…`; `ts` (RFC 3339 or Unix seconds, naive times are UTC) defaults to arrival time.
Readings are stored as the IoT stack's Node-RED flow stores MQTT readings, so both kinds
query alike: measurement `sensor_data`, tags `device` and `sensor`, numbers as floats.
`201 {"stored", "published"}` means they're in InfluxDB; each is then published to
`sensors/{device}/{sensor}` with `ts` and `"_stored_by": "p4n4-api"`, which tells Node-RED
not to store it again (needs the stack's flow from p4n4-iot with that check) while other
flows still fire. Errors: `503 influxdb_unavailable`, `503 influxdb_not_configured`,
`422 influxdb_rejected` (e.g. a field that was a number is now a string). Ingest updates
the device's `last_seen_at` (at most once a minute).

**Query.** `?device=&sensor=&field=` filter; `start` (default `-1h`) and `stop` take a
duration back from now (`-30m`, `-7d`) or an RFC 3339 time; `every=1m` aggregates into
windows with `agg` (`mean` default, `median`, `min`, `max`, `sum`, `count`, `first`, `last`);
`limit` 1–10000 (default 1000). Returns `{points: [{time, device, sensor, field, value}],
truncated}`, oldest first. The Flux is built from these checked values; raw Flux isn't
accepted.

**Stream.** `?device=&sensor=` filter. Events: `status` first (`{"mqtt": true|false}`), then
`reading` (`{device, sensor, fields, ts, received_at}`) for every `sensors/+/+` message,
devices' own included; `dropped` with a count if the client falls more than 1000 readings
behind; a keep-alive comment every 15 s. Behind nginx it isn't buffered
(`X-Accel-Buffering: no`). `EventSource` can't send `Authorization`, so read it with `fetch`.

### Edge

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/edge/metrics` | Host system metrics, in the [dashboard's edge metrics contract](https://github.com/raisga/p4n4-dashboard#edge-metrics-contract) |

Fields: `cpu_percent`, `mem_percent`, `mem_used_mb`, `mem_total_mb`, `disk_percent` (of `P4N4_API_DISK_PATH`, default `/`),
`uptime_s`, `load` (1/5/15 min) and `temp_c`. `temp_c` comes from a CPU/SoC sensor
(`cpu_thermal` on a Raspberry Pi, `coretemp`/`k10temp` on x86) and is omitted when none is
found, e.g. on macOS, Windows or in a VM. `inference_ms` is the edge runner's last pipeline
inference latency (Edge Impulse or ONNX), when the project has the edge layer and the runner
answers within 0.5 s.

### Inference (normie+ reads, operator+ runs)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/inference/runner` | Active `backend` (`edge-impulse`, `onnx` or `mock`), `model_file`, `model` (what the backend reports), `labels`, `expected_features`, and pipeline counters (`inference_count`, `last_inference_at`, `last_latency_ms`, `mqtt_connected`, `influxdb_ok`) |
| `POST` | `/api/v1/inference` | Operator+. `{values: [numbers], device?}` → `{label, confidence, anomaly_score, latency_ms, backend, mode, device, timestamp}`. Returned only: not published or stored |
| `GET` | `/api/v1/inference/results` | Stored results, newest first: `?device=&label=&backend=&start=&stop=&limit=` |

The edge stack runs one inference runner whose `MODEL_BACKEND` picks the model: an Edge
Impulse `.eim` (`backend: edge-impulse`, the runner's `mode: model`), an ONNX model
(`onnx`), or simulated results when no model is loaded (`mock`). The API works the same with
each. Before calling the runner it checks the vector's length against the model
(`input_features_count` for Edge Impulse, the input shape after the batch dimension for
ONNX): `422 wrong_feature_count`. A model failure is `422 inference_failed`; a simulated
result from a real backend (runners before p4n4-edge's fix fall back to mock on errors) is
`502 inference_failed`, never passed on as a classification. Runner down: `503
runner_unavailable`. Results come from the runner's own pipeline (`sensors/<device>/raw` →
`inference_result` in `ai_events`).

### AI agents (normie+; generation operator+)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/agents/models` | Ollama models: `name`, `size`, `modified_at`, `family`, `parameter_size`, `quantization` |
| `POST` | `/api/v1/agents/chat` | `{model, messages: [{role, content}], stream?, include_status?, options?}`: chat with an Ollama model |
| `POST` | `/api/v1/agents/generate` | Operator+. `{model, prompt, system?, stream?, include_status?, options?}`: one-shot generation |
| `GET` | `/api/v1/agents` | Letta agents: `id`, `name`, `description`, `model` |
| `POST` | `/api/v1/agents/{id}/chat` | `{message, include_status?}` → `{reply, messages: [{type, text}]}` |

**Ollama** is stateless: send the whole conversation each turn. With `stream: true` (the
default) the reply is `application/x-ndjson`, Ollama's own chunks passed through unchanged
(`message.content` or `response` per chunk, `done: true` last, an `error` chunk if the
connection breaks), so clients parse it as they would Ollama's `/api/chat`; `stream: false`
returns one JSON reply. Errors before the first chunk are normal API errors:
`404 model_not_found` (not pulled), `503 ollama_unavailable`. Generation on a Pi can take
minutes; the API waits up to 10 minutes between chunks, and nginx must not buffer
(`X-Accel-Buffering: no` is set). Disconnecting stops the generation. `options` are
Ollama's (`temperature`, `num_ctx`, …), scalars only.

**Letta** keeps each agent's conversation: send only the new message. The reply joins the
agent's assistant messages; `messages` also lists its reasoning and tool calls. The Letta
password never reaches clients. Errors: `404 agent_not_found`, `503 letta_unavailable`,
`502 upstream_auth` (wrong or missing password).

**`include_status: true`** adds a short system summary for "how is my system doing?"
questions: each stack's running/total services and anything stopped or unhealthy, and the
edge host's CPU, memory, disk, temperature, load and last inference latency. For Ollama it's
a system message (or prepended to `system`); for Letta it's prepended to the message.

### MQTT (operator+)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/mqtt/publish` | `{topic, payload?, qos?, retain?}` → `{topic, qos, retain, bytes}` |

For sending commands to devices (`commands/pump-1/set`) or test data to the sandbox
(`sandbox/sensors/...`). `payload` is sent as-is when it's a string, as JSON otherwise
(≤ 256 KB); `qos` 0 (default), 1 or 2, where 1 and 2 answer once the broker acknowledged;
`retain` keeps it for later subscribers, and a retained empty payload clears that. The
topic can't hold wildcards or start with `$`, and must pass the allow/deny filters
(`403 topic_not_allowed`): by default anything except `sensors/#` and `inference/#`, so
people can't publish readings as if they were devices (Node-RED stores those as data;
devices use `POST /telemetry` or MQTT directly). Goes through the API's MQTT connection
(`503 mqtt_unavailable` when it's down). Every publish is audited (`mqtt.publish`).

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
│   ├── influx.py            # InfluxDB HTTP: line protocol, Flux from checked params
│   ├── mqtt.py              # Persistent MQTT connection: publish, live-stream fan-out
│   ├── edge_runner.py       # Edge inference runner client (Edge Impulse / ONNX / mock)
│   ├── ai.py                # Ollama and Letta clients (streaming passthrough)
│   ├── context.py           # System status summary for AI prompts
│   ├── topics.py            # MQTT topic and filter checks, matching
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
│       ├── telemetry.py     # Ingest, query, live stream
│       ├── inference.py     # Runner info, inference, stored results
│       ├── agents.py        # Ollama models/chat/generate, Letta agents
│       ├── mqtt.py          # POST /api/v1/mqtt/publish
│       └── edge.py          # GET /api/v1/edge/metrics
└── tests/
```

Next to the package: `Dockerfile`, `docker-compose.yml` (with `docker-compose.docker.yml` for
opt-in Docker access) and `.env.example`; see [Running in Docker](#running-in-docker).

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

The API runs either **on the host** or **in a container** on `p4n4-net`
([Running in Docker](#running-in-docker)). Stack status, control and logs shell out to
`docker compose` inside each stack directory, so they need the Docker CLI (included in the
image) and the Docker daemon (on the host: your user's access; in a container: the mounted
socket, opt-in).

On the host, the API binds `127.0.0.1` and reaches the stacks on their published ports. For
the dashboard's container to reach a host-run API, bind the bridge gateway
(`P4N4_API_HOST=172.17.0.1`, or `0.0.0.0` behind a firewall) and set
`P4N4_API_TRUSTED_PROXIES=172.16.0.0/12`. The container avoids both: on `p4n4-net`, the
dashboard uses `http://p4n4-api:8000`. `p4n4 up --api` in the CLI is planned.

---

## Security

- **JWT** — HS256 signed tokens. Access tokens expire in 1 hour; refresh tokens in 7 days. Roles are `normie`, `operator` and `admin` for people (ranked), and `device` (separate: devices can only do device things). The role is read from the database on every request, not trusted from the token; device tokens are re-checked too, so rotating a key, disabling or deleting a device ends its tokens at once.
- **Refresh rotation** — each refresh token works once. Reusing one revokes every token from that sign-in. Password changes sign the user out everywhere. Signing out revokes that sign-in's refresh **and** access tokens at once (access tokens carry their sign-in's ID, `sid`, checked on every request); other sign-ins are untouched.
- **Passwords** — argon2id, at least 10 characters. Unknown usernames take as long to reject as wrong passwords.
- **API keys** — 256-bit random secrets, stored as argon2id hashes; plaintext shown only once, at registration and at rotation. Unknown keys take as long to reject as wrong ones. Key exchange shares the sign-in rate limit.
- **Secrets** — never stored in the database; all upstream credentials are injected via environment variables.
- **Rate limiting** — `/auth/token`, `/auth/refresh` and `/auth/password`: 10 attempts per client address, then one every 6 s (in-memory token bucket; `429` + `Retry-After`). Behind a reverse proxy, set `P4N4_API_TRUSTED_PROXIES` or every client shares the proxy's limit; the client is the rightmost `X-Forwarded-For` entry that isn't a trusted proxy. `p4n4-api` turns off uvicorn's own proxy-header handling (which trusts `127.0.0.1`); when running `uvicorn` directly, pass `--no-proxy-headers` for the same behaviour. There's deliberately no per-username limit: it would let anyone lock a known user out.
- **CORS** — off by default; allowlist via `P4N4_API_CORS_ORIGINS`. Credentials (cookies) are never allowed: auth uses the `Authorization` header.
- **Port exposure** — the compose file publishes `8000` on `127.0.0.1` only; containers on `p4n4-net` don't need it. To expose the API beyond the host, front it with a TLS reverse proxy (nginx, Caddy, Traefik) rather than binding `0.0.0.0`.
- **Docker socket** — off by default in the container. With `docker-compose.docker.yml`, the API (and so any admin token) can control every container on the host, which is root-equivalent.

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
