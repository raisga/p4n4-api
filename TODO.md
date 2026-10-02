# TODO

_Last updated: 2026-10-01_

Pending work for `p4n4-api`. The [README](README.md) describes the target surface. This file tracks what's built, what's missing, and the order to build it in. The main consumer is [`p4n4-dashboard`](../dashboard/), so items it's waiting on are marked **🖥 dashboard**.

## Current status (v0.1)

| Area | Status | Notes |
|------|--------|-------|
| Project info / validation | ✅ Working | `GET /api/v1/project`, `GET /api/v1/project/validate`, built on `p4n4-lib` (flat and multi-layer layouts). |
| Stack status | ✅ Working | `GET /api/v1/stacks[/{stack}]` shells out to `docker compose ps` per stack, with image, version, ports and uptime per service, and `503` when Docker is unreachable. The dashboard's Services, Home and Clients tabs already use it, and fall back to port probes when it's unreachable. |
| Health | ✅ Working | `/health`, `/ready` (project + Docker), `/api/v1/version`. |
| Auth | ✅ Working (people) | Username/password sign-in → JWT, `operator`/`admin` roles, refresh rotation, `p4n4-api users` CLI. Device API keys come with M3. **The dashboard doesn't sign in yet**, so its status calls now get `401` and it falls back to port probes. |
| Upstream proxies | ❌ Missing | Nothing yet for InfluxDB, MQTT, Ollama, Letta or the Edge Impulse runner. |
| Edge metrics | ✅ Working | `GET /api/v1/edge/metrics` via `psutil`, in the dashboard's contract. `inference_ms` waits for M6. |
| CORS | ✅ Working | `P4N4_API_CORS_ORIGINS` allowlist; off by default. |
| Packaging | ❌ Missing | Runs on the host only and binds `127.0.0.1`. No Dockerfile or compose file. |
| Tests / CI | ✅ Working | 64 tests with synthetic projects and a stubbed Compose client. CI runs ruff and pytest on 3.11–3.13. |

## Housekeeping

- [ ] The README links to `DESIGN.md` twice, but it doesn't exist: `API_DESIGN.md` (the Rust draft) was deleted in `290b8f8`. Either write a Python/FastAPI `DESIGN.md` (schemas, error codes, middleware) or remove the links.
- [ ] Commit or drop the untracked `.gitattributes`.
- [ ] Add response models (Pydantic) for the existing endpoints so `/openapi.json` documents real schemas instead of `dict`. The dashboard parses `stacks[].services[].{name,state,health}`, so treat that shape as a contract and test it.
- [ ] Move settings to `pydantic-settings` (the README already names it) before the env var list grows.
- [ ] Standard error body (`{"error": {"code", "message"}}`) and exception handlers, so clients get one error shape.
- [x] `compose.ps` failures: Docker CLI missing, daemon down or Compose missing now return `503` from `/stacks` (checked with `docker version` first), so the dashboard falls back to port probes instead of showing every service as stopped.
  - [ ] Root cause is in `p4n4-lib`: `compose.ps` ignores Compose's exit code and returns `[]`. Make it raise, then drop the extra `docker version` call per request.
- [ ] Structured logging with a request ID (`X-Request-ID` in and out).

## Milestones

In dependency order. Each one should ship with tests, README updates and a CHANGELOG entry.

### M1: Dashboard unblockers (no auth needed)

Small items the dashboard needs now. They're read-only or config-only, so they don't wait for auth.

- [x] **🖥 dashboard** `GET /api/v1/edge/metrics`, returning the contract in `dashboard/README.md#edge-metrics-contract`:
  - `cpu_percent`, `mem_percent` (required); `mem_used_mb`, `mem_total_mb`, `disk_percent`, `temp_c`, `uptime_s`, `load`, `inference_ms` (optional: omit rather than send `null` when unknown).
  - The API runs on the edge host in v0.1, so `psutil` on the host is enough. `temp_c` comes from a list of known CPU/SoC sensors (`cpu_thermal`, `coretemp`, `k10temp`, …), then any sensor named `*cpu*`/`*soc*`. `thermal_zone0` and the first sensor listed are deliberately *not* used: on x86 they're often `acpitz`, a board sensor.
  - [ ] Check `temp_c` on a real Raspberry Pi 5 and on a Jetson, and add their sensor names if they're missing.
  - [ ] `disk_percent` is for `/`. Consider a `P4N4_API_DISK_PATH` setting if data lives on another volume (e.g. an SSD for InfluxDB).
  - `inference_ms` from the Edge Impulse runner's last result, once M6 exists. Leave it out until then.
  - Once containerized, host metrics need `/proc` and `/sys` mounted read-only (or `pid: host`). Document it.
- [x] **🖥 dashboard** `P4N4_API_CORS_ORIGINS`: FastAPI `CORSMiddleware` with an explicit allowlist and no `*` alongside credentials. Needed for the dashboard's web dev server and for the **Clients** tab on web, which calls other deployments' APIs cross-origin (`dashboard/SERVICE_INTEGRATION.md` §5.3).
- [x] **🖥 dashboard** Richer stack status: `image`, `version`, `status`, `exit_code`, `ports`, `started_at`, `uptime_s` per service (uptime from one `docker inspect` per stack).
  - [ ] Dashboard side: show these in Services (its `ComposeService` ignores unknown fields, so nothing breaks meanwhile).
- [x] `GET /ready`: checks that the project resolves and Docker is reachable. Add InfluxDB and MQTT checks once those clients exist.
- [x] `GET /api/v1/version` so the dashboard and CLI can detect API features instead of guessing.

### M2: Auth and roles

Gates every state-changing endpoint below.

- [x] HS256 JWTs (`P4N4_API_JWT_SECRET`, at least 32 characters, or generated once into the data dir). Access token 1 h, refresh token 7 d.
- [x] People sign in with **operator/admin accounts** (username + argon2id password) in SQLite (`P4N4_API_DATA_DIR/api.db`). Admin-issued personal API keys were the alternative; API keys stay for devices (M3).
- [x] `POST /api/v1/auth/token`, `/auth/refresh` (single-use refresh tokens; reuse revokes the whole sign-in), `/auth/logout`, `GET /auth/me`.
- [x] `require_role("operator")` / `require_role("admin")`, ranked. `/health`, `/ready`, `/api/v1/version` and the docs stay public; `/project`, `/stacks` and `/edge/metrics` need operator+. Role and a password-change counter are read from the DB on every request, so changes apply immediately.
- [x] Bootstrap: `p4n4-api users add admin --role admin`; the server logs a hint at startup while there are no users. Also `users list|passwd|role|remove`.
- [x] Rate limiting on `/auth/token` and `/auth/refresh` (10 per client address, then 1 per 6 s).
- [x] Tests: expired, forged (wrong key, `alg: none`, garbage), wrong-type and revoked tokens; role ranking; refresh reuse; password/role changes and deleted users; rate limit; CLI.
- [ ] **🖥 dashboard** Sign in: replace the role picker (`lib/core/session.dart`) with a username/password form, keep tokens in `flutter_secure_storage` (`secretKeys`), send `Authorization: Bearer` on API calls, refresh on `401`, and take the role from `/auth/me`. Until this ships, run the API with `P4N4_API_AUTH=off` to keep the dashboard's API status working.
- [ ] **🖥 dashboard** Map roles: the dashboard's *client* view ↔ `operator`, *admin* ↔ `admin`. Decide whether a separate read-only `viewer` role is needed for client accounts before giving operators state-changing rights in M4.
- [ ] Admin user-management endpoints (`GET/POST/PATCH/DELETE /api/v1/users`), so admins can create client accounts from the dashboard instead of the server's shell. Plus self-service `POST /api/v1/auth/password`.
- [ ] Behind a reverse proxy (the dashboard's nginx), every client shares one address, so one attacker can fill the sign-in rate limit for everyone. Add a trusted-proxy setting that reads `X-Forwarded-For`, and/or also limit per username.
- [ ] `p4n4 init` could create the first admin (and print its password once) so a new project needs no extra step. Coordinate with `p4n4-cli`.
- [ ] Sign-out leaves the access token valid until it expires (≤ 1 h). If that's too long, add a deny-list of revoked access-token IDs, or shorten `ACCESS_TTL`.

### M3: Device registry

- [ ] Devices table in the existing SQLite database (`p4n4_api/db.py`: append to `MIGRATIONS`). M2 used stdlib `sqlite3` with versioned migrations instead of the SQLAlchemy + Alembic originally planned: two dependencies fewer on a Pi, and enough for a few tables. Revisit if the schema grows. In a container, `P4N4_API_DATA_DIR` must be a volume.
- [ ] Add `device` to the roles (outside the operator/admin ranking: devices can only ingest) and accept `{"api_key": ...}` at `/auth/token`.
- [ ] `GET/POST /api/v1/devices`, `GET/PATCH/DELETE /api/v1/devices/{id}` (admin writes, operator reads, paginated).
- [ ] API key rotation. The README has it as `GET /devices/{id}/key`; it changes state, so make it `POST /devices/{id}/key`.
- [ ] Keys stored as argon2id hashes, plaintext shown once.

### M4: Stack control and logs

The README defers these until auth lands. The dashboard's stack-controls menu is already built and disabled, waiting for them.

- [ ] **🖥 dashboard** `POST /api/v1/stacks/{stack}/{up|down|restart}` (admin), wrapping `p4n4_lib.compose.up/down`. Run as a background job: return `202` with a job ID plus `GET /api/v1/jobs/{id}`, since `up --pull` can take minutes on a Pi.
- [ ] **🖥 dashboard** Per-service restart, if Compose wrappers for it are added to `p4n4-lib`.
- [ ] **🖥 dashboard** `GET /api/v1/stacks/{stack}/logs?service=&tail=&follow=`: SSE when `follow=true`, wrapping `p4n4_lib.compose.logs`. Bound `tail` and kill the subprocess when the client disconnects.
- [ ] Respect layer order (`layout.ordered()`) for "all stacks" operations: up in order, down in reverse.
- [ ] Audit log of who ran what and when.

### M5: Telemetry

- [ ] Async InfluxDB client (`INFLUXDB_URL`, `INFLUXDB_TOKEN`, org, bucket).
- [ ] `POST /api/v1/telemetry` (device role): batch JSON → line protocol → InfluxDB, and publish to MQTT so Node-RED flows still fire.
- [ ] `GET /api/v1/telemetry` (operator): query params (`device`, `measurement`, `field`, `start`, `stop`, `every`, `agg`) compiled to Flux. Never accept raw Flux from clients.
- [ ] MQTT client (`aiomqtt`) with one persistent subscription, started and stopped in the app lifespan, with reconnects.
- [ ] **🖥 dashboard** `GET /api/v1/telemetry/stream` (SSE), fed by that subscription through a broadcast queue, with filters and heartbeats. Live sensor values for Home and Edge.
- [ ] **🖥 dashboard** Metrics history for the Edge tab's planned longer ranges and CSV export: either store `edge/metrics` samples in InfluxDB, or read them from an existing exporter.

### M6: Inference

- [ ] Edge Impulse runner client (`EDGE_RUNNER_URL`).
- [ ] `POST /api/v1/inference`: forward a feature vector, return the classification and timing.
- [ ] `GET /api/v1/inference/results`: recent results from InfluxDB `ai_events`.
- [ ] Feed the last inference latency into `edge/metrics` (`inference_ms`).

### M7: AI agents

The dashboard calls Ollama and Letta directly today. Routing through the API puts auth in front of them and removes the Letta password from the client.

- [ ] **🖥 dashboard** `GET /api/v1/agents/models`: Ollama model list (the dashboard uses `/api/tags`).
- [ ] **🖥 dashboard** `POST /api/v1/agents/generate` and a chat variant with **streaming** (NDJSON or SSE, matching what the dashboard parses from Ollama `/api/chat`). Disable buffering and allow long timeouts, since generations on a Pi are slow.
- [ ] **🖥 dashboard** `GET /api/v1/agents` and `POST /api/v1/agents/{id}/chat` for Letta (`LETTA_URL`, `LETTA_SERVER_PASSWORD` kept server side).
- [ ] **🖥 dashboard** Optional "system context" for the dashboard's planned *include system status* button: an endpoint (or a flag on chat) that adds current stack status and edge readings to the prompt.

### M8: MQTT publish

- [ ] `POST /api/v1/mqtt/publish` (operator) with QoS and retain. Optionally restrict topics with an allowlist or prefix.

### M9: Fleet and alerts

Needed for the dashboard's *Fleet and alerts* roadmap. Scope it before starting.

- [ ] **🖥 dashboard** Deployment list served by the API instead of the dashboard's local settings (Clients tab). This is a multi-deployment concern; decide whether it belongs in each p4n4-api or in a separate fleet service.
- [ ] **🖥 dashboard** Alert rules (e.g. temperature > X, service down > N minutes) evaluated server side, with an alert feed (SSE) and incident history of status changes.
- [ ] **🖥 dashboard** Push notifications (FCM/APNs or ntfy), sent by the API rather than polled by the app.

### M10: Packaging and deployment

- [ ] `Dockerfile` (multi-stage, multi-arch amd64/arm64, non-root) and `docker-compose.yml` on the external `p4n4-net` network.
- [ ] Stack control from inside a container needs the Docker socket and the project directory mounted. That gives the container root-equivalent access to the host: document it, keep it opt-in, and consider a socket proxy that allows only the Compose calls in use.
- [ ] Once containerized, the dashboard's nginx upstream changes from `host.docker.internal:8000` to `http://p4n4-api:8000` (`dashboard/SERVICE_INTEGRATION.md` §3.1).
- [ ] Until then, document `P4N4_API_HOST=0.0.0.0` (or the bridge gateway IP) so the dashboard container can reach the host API.
- [ ] Register an `api` layer in `p4n4-lib` and `p4n4 up --api` in the CLI.
- [ ] Image publishing workflow (GHCR, semver tags, SBOM), matching the dashboard's `image.yml` plan.

### M11: Hardening and observability

- [ ] `/metrics` (Prometheus): request counts, latency histograms, upstream call stats.
- [ ] Global rate limiting, request size limits, and timeouts on every upstream call.
- [ ] Integration tests against real containers (Mosquitto, InfluxDB, Ollama stub), in a separate CI job.
- [ ] Security notes: TLS via reverse proxy, removing the `8000` host binding in production, secret handling.
- [ ] Docs page in p4n4-docs; keep the Swagger UI descriptions and examples current.

## Open questions

- **Long-running work:** in-process background tasks are enough for one Pi. Is a job table in SQLite needed so jobs survive restarts?
- **Sync vs async:** routes are sync and run in the threadpool because `p4n4_lib.compose` uses `subprocess`. Upstream clients (InfluxDB, MQTT, HTTP) should be async. Keep both styles, or wrap `compose` in `anyio.to_thread`?
- **Proxy vs re-shape:** for Ollama/Letta, pass the upstream API through as-is (less work, the dashboard client barely changes) or define p4n4-owned schemas (stable when upstreams change)?
- **One API per deployment vs fleet:** the Clients tab and alerts want a cross-deployment view. Keep p4n4-api single-deployment and build fleet features elsewhere?
- **n8n:** the README doesn't plan anything for n8n. Is a thin proxy needed to trigger workflows from the CLI or dashboard?
