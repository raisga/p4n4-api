# TODO

_Last updated: 2026-10-01_

Pending work for `p4n4-api`. The [README](README.md) describes the target surface. This file tracks what's built, what's missing, and the order to build it in. The main consumer is [`p4n4-dashboard`](../dashboard/), so items it's waiting on are marked **🖥 dashboard**.

## Current status (v0.1)

| Area | Status | Notes |
|------|--------|-------|
| Project info / validation | ✅ Working | `GET /api/v1/project`, `GET /api/v1/project/validate`, built on `p4n4-lib` (flat and multi-layer layouts). |
| Stack status | ✅ Working | `GET /api/v1/stacks[/{stack}]` shells out to `docker compose ps` per stack, with image, version, ports and uptime per service, and `503` when Docker is unreachable. The dashboard's Services, Home and Clients tabs already use it, and fall back to port probes when it's unreachable. |
| Health | ✅ Working | `/health`, `/ready` (project + Docker), `/api/v1/version`. |
| Auth | ✅ Working | People: username/password → JWT, `operator`/`admin` roles, refresh rotation, `p4n4-api users` CLI and admin `/api/v1/users` endpoints. Devices: API key → JWT, `device` role. The dashboard signs in; it has no user- or device-management screen yet. |
| Device registry | ✅ Working | `/api/v1/devices` CRUD, paginated, with API key rotation (M3). |
| Stack control | ✅ Working | Up/down/restart (stack, all, or one service) as background jobs, container logs with SSE follow, audit log (M4). |
| Upstream proxies | ❌ Missing | Nothing yet for InfluxDB, MQTT, Ollama, Letta or the Edge Impulse runner. |
| Edge metrics | ✅ Working | `GET /api/v1/edge/metrics` via `psutil`, in the dashboard's contract. `inference_ms` waits for M6. |
| CORS | ✅ Working | `P4N4_API_CORS_ORIGINS` allowlist; off by default. |
| Packaging | ❌ Missing | Runs on the host only and binds `127.0.0.1`. No Dockerfile or compose file. |
| Tests / CI | ✅ Working | 191 tests with synthetic projects, a stubbed Compose client for status and a fake `docker compose` script for control and logs. CI runs ruff and pytest on 3.11–3.13. |

## Housekeeping

- [x] The README linked to a `DESIGN.md` that doesn't exist (`API_DESIGN.md`, the Rust draft, was deleted in `290b8f8`). Links removed: schemas come from the OpenAPI spec (see response models below), the roadmap is this file.
- [x] `.gitattributes` (LF line endings, binary types) is committed (`112b56f`).
- [x] Response models (Pydantic) for every endpoint, so `/openapi.json` documents real schemas instead of `dict`. A test fails if any endpoint goes back to an untyped body, and another pins the dashboard's contracts (`stacks[].services[].{name,state,health}`, edge metrics' required fields, `/ready`'s `503` body). Manifest `template`/`dashboard` blocks stay free-form objects (p4n4-lib validates them).
- [x] Settings on `pydantic-settings` (`p4n4_api/config.py`): typed, validated at startup (a bad `P4N4_API_PORT` or `P4N4_API_AUTH=maybe` fails with the variable's name), empty variables count as unset. New upstream settings (M5–M7) are one field each. `P4N4_API_AUTH` now also accepts `no`, but rejects unknown values instead of treating them as on.
- [x] Standard error body (`{"error": {"code", "message"}}`, plus `fields` for request validation) from exception handlers in `p4n4_api/errors.py`, for HTTP errors, routing `404`/`405`, validation `422` and unhandled `500`s (no internals leaked). `ApiError` sets specific codes (`user_exists`, `last_admin`); the OpenAPI spec documents the shape for 4XX/5XX. The dashboard only branches on status codes, so nothing breaks there.
  - [ ] Unhandled `500`s are produced outside `CORSMiddleware` (Starlette's `ServerErrorMiddleware`), so a browser on another origin sees a CORS error instead of the body. Only matters for cross-origin clients; fix if it gets in the way of debugging.
- [x] `compose.ps` failures: Docker CLI missing, daemon down or Compose missing now return `503` from `/stacks` (checked with `docker version` first), so the dashboard falls back to port probes instead of showing every service as stopped.
  - [ ] Root cause is in `p4n4-lib`: `compose.ps` ignores Compose's exit code and returns `[]`. Make it raise, then drop the extra `docker version` call per request.
- [x] Structured logging with a request ID (`X-Request-ID` in and out): `p4n4_api/logs.py`. A sane incoming ID is kept, otherwise one is generated; it's on every response and every log line during the request. One access line per request (method, path, status, duration, real client behind trusted proxies). `P4N4_API_LOG_FORMAT=text|json`, `P4N4_API_LOG_LEVEL`. `p4n4-api serve` applies the format to uvicorn's lines too and replaces uvicorn's access log; plain `uvicorn p4n4_api.main:app` keeps uvicorn's logging.
  - [ ] Unhandled `500`s are built by Starlette's `ServerErrorMiddleware`, outside ours, so they lack the `X-Request-ID` header (the log line still has it). Same root cause as the CORS note above; one fix (an outermost handler of our own) covers both.
  - [ ] **🖥 dashboard** Send `X-Request-ID` on API calls and show it in error messages, so a report can be matched to the server log.

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
- [x] **🖥 dashboard** Sign in: username/password form, refresh token in secure storage, `Authorization: Bearer` on API calls (`X-Upstream-Authorization` behind the dashboard's proxy), single-flight refresh on `401`, role from the token response. Role picker only when auth is off or the API is unreachable.
- [x] **🖥 dashboard** Map roles (done as below; the `viewer` question is still open): the dashboard's *client* view ↔ `operator`, *admin* ↔ `admin`. Decide whether a separate read-only `viewer` role is needed for client accounts before giving operators state-changing rights in M4.
- [x] Admin user-management endpoints (`GET/POST/PATCH/DELETE /api/v1/users`), so admins can create client accounts from the dashboard instead of the server's shell. The API refuses to demote or remove the last admin (`409`); the CLI still can, as the recovery path.
- [x] Self-service `POST /api/v1/auth/password`: needs the current password, is rate-limited like sign-in, signs out every other sign-in and returns a new token pair.
- [ ] **🖥 dashboard** Users screen for admins (list, add client account, change role, reset password, remove) and a *Change password* form for everyone. On `/auth/password` success, replace the stored tokens with the returned pair.
- [x] Behind a reverse proxy (the dashboard's nginx), every client shares one address, so one attacker can fill the sign-in rate limit for everyone. `P4N4_API_TRUSTED_PROXIES` (IPs/CIDRs) now makes the limit per client via `X-Forwarded-For` (uvicorn's `ProxyHeadersMiddleware`: rightmost untrusted hop). Invalid entries fail at startup. `p4n4-api` runs uvicorn with `proxy_headers=False`, so it no longer trusts the header from `127.0.0.1` by default.
  - No per-username limit: it would let anyone lock out a known user (e.g. `admin`) by failing sign-ins on purpose. Revisit only with a lockout-free design (e.g. counting failures per username + client pair) if distributed guessing shows up.
  - [ ] **🖥 dashboard** Set `P4N4_API_TRUSTED_PROXIES=172.16.0.0/12` in the API's documented setup next to `P4N4_API_HOST` (dashboard `SERVICE_INTEGRATION.md`), and to `p4n4-dashboard` once the API is containerized (M10).
- [ ] `p4n4 init` could create the first admin (and print its password once) so a new project needs no extra step. Coordinate with `p4n4-cli`.
  - [x] API side: `p4n4-api users bootstrap [username]` creates an admin with a generated password and prints it once, only while there are no users (checked in the insert itself, so concurrent runs are safe). Idempotent, so an installer or container entrypoint can run it every time.
  - [ ] CLI side: blocked on the `api` layer (M10). Today the API keeps its database in its own data dir, outside the project, and `p4n4-cli` doesn't install or know about it. Once the layer exists, `p4n4 init --api` (or `p4n4 up --api` on first start) runs `p4n4-api users bootstrap` in the API's container and passes its output through.
- [x] Sign-out left the access token valid until it expired (≤ 1 h). Access tokens now carry their sign-in's ID (`sid`, the refresh-token family), and every request checks that the sign-in still has refresh tokens. So sign-out, refresh-token reuse, password changes and user removal end its access tokens at once, with no deny-list table and no shorter `ACCESS_TTL`. Tokens from before this change have no `sid` and get `401`; the dashboard's refresh-on-`401` gets a new one.
  - `token_gen` is now redundant with this (a password change deletes the user's refresh tokens), but cheap; drop it in a later migration if the schema is touched anyway.

### M3: Device registry

- [x] Devices table: migration 2 in `p4n4_api/db.py` (existing databases upgrade on next open). Still stdlib `sqlite3` with versioned migrations rather than SQLAlchemy + Alembic: two dependencies fewer on a Pi, and enough for a few tables. Revisit if the schema grows. In a container, `P4N4_API_DATA_DIR` must be a volume.
- [x] `device` role, outside the operator/admin ranking (devices pass only `require_role("device")`, people never do). `/auth/token` takes `{"api_key": ...}` and returns an access token only; no refresh token, since the device keeps its key. Device tokens use the subject `device:<id>` (`:` can't be in a username, so no clash) and are re-checked per request: removal, disabling or rotation end them at once.
- [x] `GET/POST /api/v1/devices`, `GET/PATCH/DELETE /api/v1/devices/{id}` (admin writes, operator reads; `limit`/`offset` pagination with `total`). IDs are lowercase slugs, since they'll tag telemetry in M5.
- [x] API key rotation as `POST /devices/{id}/key` (the README's `GET` changed state).
- [x] Keys: `p4n4_<12-hex key id>_<256-bit secret>`, argon2id-hashed, plaintext shown once (registration, rotation). The key id finds the device without trying every hash; the prefix lets secret scanners spot leaks.
- [ ] `last_seen_at` is the last key exchange (hourly at most per device). Update it on telemetry ingest too once M5 exists, throttled so every reading isn't a database write.
- [ ] Many devices behind one NAT share the sign-in rate limit (10, then 1 per 6 s, i.e. ~600 key exchanges an hour). Fine for a small fleet; give key exchange its own, larger bucket if fleets grow.
- [ ] `p4n4-api devices` CLI (list/add/rotate/remove), like `users`, for setups without the dashboard. Optional: curl against the API works.
- [ ] **🖥 dashboard** Devices screen: list with `last_seen_at`, register (show the key once with a copy button), rotate, disable, remove.

### M4: Stack control and logs

API side done; the dashboard's stack-controls menu (built and disabled) can now be wired up.

- [x] `POST /api/v1/stacks/{stack}/{up|down|restart}` (admin) as background jobs: `202` + `Location`, `GET /api/v1/jobs[/{id}]` (operator+) with status and the last 500 lines of output. One worker, so Compose operations never overlap; an identical job still queued is reused (double clicks). 30-minute watchdog. `up?pull=true`; `down` never passes `-v`.
- [x] Per-service restart: `POST /api/v1/stacks/{stack}/services/{service}/restart`, checked against `docker compose config --services` (so stopped services count, and nothing else reaches Compose's argv).
- [x] `GET /api/v1/stacks/{stack}/logs?service=&tail=&follow=` (admin: logs can hold secrets): JSON, or SSE with `follow=true` (keep-alives every 15 s, `end` event with the exit code, `X-Accel-Buffering: no` for nginx). `tail` bounded to 1–5000; Compose is killed when the client disconnects (tested).
- [x] `all` stacks: up in dependency order (`compose_dirs`), down in reverse, stopping at the first failure.
- [x] Audit log (SQLite, migration 3; `GET /api/v1/audit`, admin): stack actions when queued and finished, user and device changes in the same transaction as the change. Entries carry the request ID, including for the job's own "finished" entry.
- [ ] `p4n4_lib.compose.up/down/logs` print to the terminal and return only an exit code, so the API builds Compose's arguments itself (`jobs._commands`, `control._logs_cmd`) using `compose_cmd()`. Move argument builders (or output-capturing variants) into `p4n4-lib` so the CLI and API can't drift (e.g. the docker-compose v1 `pull` workaround is now in both).
- [ ] Jobs are in memory (open question below). If an API restart mid-`up` matters, persist them in SQLite and mark running ones `interrupted` at startup.
- [ ] **🖥 dashboard** Enable the stack-controls menu: call the actions, poll the job (or show its output live), and a logs viewer using the SSE stream (`EventSource` can't send `Authorization`, so read the stream with `fetch`).

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
- [ ] Register an `api` layer in `p4n4-lib` and `p4n4 up --api` in the CLI. First start runs `p4n4-api users bootstrap` and shows the generated admin password (see M2).
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
