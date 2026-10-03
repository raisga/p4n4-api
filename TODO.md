# TODO

_Last updated: 2026-10-02_

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
| Telemetry | ✅ Working | Device ingest to InfluxDB (+ marked MQTT publish), Flux queries, live SSE stream from MQTT (M5). |
| Upstream proxies | ✅ Working | InfluxDB, MQTT (M5), the edge runner (M6), Ollama and Letta (M7). |
| MQTT publish | ✅ Working | `POST /api/v1/mqtt/publish` with QoS/retain, topic allow/deny lists, audited (M8). |
| AI agents | ✅ Working | Ollama models, streamed chat/generate, Letta agents, optional system status in prompts (M7). |
| Edge metrics | ✅ Working | `GET /api/v1/edge/metrics` via `psutil`, in the dashboard's contract, with the runner's `inference_ms` (M6). |
| Inference | ✅ Working | Edge runner (Edge Impulse, ONNX or mock backend): model info, on-demand inference, stored results (M6). |
| CORS | ✅ Working | `P4N4_API_CORS_ORIGINS` allowlist; off by default. |
| Packaging | ✅ Working | Multi-arch, non-root image (`Dockerfile`, GHCR workflow) and `docker-compose.yml` on `p4n4-net`; Docker socket opt-in (M10). Not yet a p4n4 layer in the CLI. |
| Tests / CI | ✅ Working | 340 tests with synthetic projects, a stubbed Compose client for status and a fake `docker compose` script for control and logs. CI runs ruff and pytest on 3.11–3.13. |

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
  - [x] `P4N4_API_DISK_PATH` picks the filesystem for `disk_percent` (default `/`; omitted if the path is missing).
  - `inference_ms` from the Edge Impulse runner's last result, once M6 exists. Leave it out until then.
  - Containerized, `/proc` and `/sys` already show the host's CPU, memory, load, uptime and temperatures (checked on x86: `temp_c` present); no extra mounts. `disk_percent` is Docker's data root unless a disk is mounted and `P4N4_API_DISK_PATH` set. Documented in the README.
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
  - [ ] **🖥 dashboard** Set `P4N4_API_TRUSTED_PROXIES=172.16.0.0/12` in the API's documented setup next to `P4N4_API_HOST` (dashboard `SERVICE_INTEGRATION.md`). The API's compose file already sets it (M10).
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

- [x] Async InfluxDB client over `httpx` (`p4n4_api/influx.py`; no `influxdb-client`). Token, org and bucket default to the project's `iot/.env`, so a host-run API needs no setup; `P4N4_API_INFLUXDB_*` override. `/ready` checks InfluxDB (required) and reports MQTT (not required) when the project has the iot layer.
- [x] `POST /api/v1/telemetry` (device role): writes InfluxDB itself with Node-RED's schema (`sensor_data`, tags `device`/`sensor`, numbers as floats so field types never conflict) and the device's timestamps, `201` once stored; then publishes to `sensors/{device}/{sensor}` with `"_stored_by": "p4n4-api"`. The IoT stack's Node-RED flow skips marked messages (changed in `stacks/iot`, both format functions), so nothing is stored twice and other flows still fire. Updates `last_seen_at` (throttled, closes the M3 item).
- [x] `GET /api/v1/telemetry` (operator): `device`, `sensor`, `field`, `start`, `stop`, `every`, `agg`, `limit` compiled to Flux; values are validated or quoted (injection tests). The README's `measurement` parameter became `sensor`: everything is in `sensor_data`, and the sensor is a tag.
- [x] MQTT (`aiomqtt`): one connection, started and stopped with the app, subscribed to `sensors/+/+`, reconnecting with backoff (1–30 s, logs on state changes only). Tested against a real throwaway mosquitto (skipped when it isn't installed).
- [x] `GET /api/v1/telemetry/stream` (SSE, operator): per-client bounded queue (slow clients drop their oldest readings and get a `dropped` event), `device`/`sensor` filters, `status` event, keep-alives. Readings the API ingests while the broker is down still reach live streams.
- [ ] **🖥 dashboard** Live sensor values for Home and Edge from the stream (read with `fetch`: `EventSource` can't send `Authorization`), and history charts from `GET /telemetry`.
- [ ] Deploy the updated Node-RED flow (p4n4-iot) together with this API version: an older flow stores API readings a second time (with arrival time). `p4n4 update` / re-scaffolding picks it up; existing projects need the new `flows.json`.
- [ ] Inference results (`inference/{device}/result`, M6) and the `sandbox/` topics aren't ingested or streamed yet.
- [ ] Batches are written in one request; InfluxDB rejects the whole batch on one bad point (`422 influxdb_rejected`). If partial success is needed, retry point by point and report which failed.
- [ ] **🖥 dashboard** Metrics history for the Edge tab's planned longer ranges and CSV export: either store `edge/metrics` samples in InfluxDB, or read them from an existing exporter.

### M6: Inference

- [x] Runner client (`p4n4_api/edge_runner.py`, `P4N4_API_EDGE_RUNNER_URL`) for all of the runner's backends: Edge Impulse `.eim`, ONNX and mock. `GET /api/v1/inference/runner` shows which is active, the model, labels and the expected feature count (Edge Impulse `input_features_count`, ONNX input shape). Model info is cached 30 s.
- [x] `POST /api/v1/inference` (operator): checks the vector length against the model first (`422 wrong_feature_count`), returns label, confidence, anomaly score, latency and backend. Not published or stored, like the runner's own endpoint.
  - The runner answered a model failure on `/api/v1/infer` with a simulated (mock) result. Fixed in `stacks/edge` (HTTP path only: the MQTT pipeline still falls back so it keeps running): it now returns `422` with the error. The API also refuses a mock result from a real backend (`502`), for runners without the fix.
- [x] `GET /api/v1/inference/results` (operator): `ai_events` / `inference_result`, pivoted to one row per result, newest first; `device`, `label`, `backend`, time range, `limit`. Bucket from the project's `edge/.env`.
- [x] `edge/metrics` `inference_ms`: the runner's `/health` now reports `last_latency_ms` (added in `stacks/edge`); read with a 0.5 s timeout, only for projects with the edge layer. `/ready` reports the runner (not required).
- [ ] Deploy the updated runner (p4n4-edge) with this API version for `inference_ms` and proper `422`s; older runners work, without `inference_ms`, and their mock fallbacks surface as `502`.
- [ ] **🖥 dashboard** Edge tab: runner backend/model card, a "classify" form, recent results, and `inference_ms` on the metrics chart.
- [ ] Inference results also reach MQTT (`inference/{device}/result`); add them to the live stream (`/telemetry/stream` subscribes to `sensors/+/+` only).

### M7: AI agents

The dashboard calls Ollama and Letta directly today. Routing through the API puts auth in front of them and removes the Letta password from the client.

- [x] `GET /api/v1/agents/models`: Ollama's `/api/tags`, reshaped (`name`, `size`, family, parameter size, quantization). `models[].name` is where the dashboard already reads it.
- [x] `POST /api/v1/agents/chat` and `/generate`: Ollama's NDJSON chunks streamed through unchanged, so the dashboard's `OllamaClient` parser works as is (only its URLs and auth change); `stream: false` for one JSON reply. Upstream errors before the first chunk become API errors (`model_not_found`, `ollama_unavailable`); a connection lost mid-reply ends with an `error` chunk. 10-minute read timeout, `X-Accel-Buffering: no`, and disconnecting closes the Ollama request (tested). Checked against a real Ollama (model list, not-found error).
- [x] `GET /api/v1/agents` and `POST /api/v1/agents/{id}/chat` for Letta: password from `ai/.env` (`LETTA_SERVER_PASSWORD`) or `P4N4_API_LETTA_PASSWORD`, sent only API → Letta. Replies reshaped to `{reply, messages: [{type, text}]}`.
- [x] `include_status: true` on all three chats: stack running/total with stopped and unhealthy services, edge host metrics and last inference latency, as a system message (Ollama) or before the message (Letta). `/ready` reports Ollama and Letta (not required) for projects with the ai layer.
- [ ] **🖥 dashboard** Point `OllamaClient` at `/api/v1/agents/models` (it reads `models[].name`) and `/api/v1/agents/chat` with `Authorization: Bearer`, and `LettaClient` at `/api/v1/agents` (`agents[]`) and `/{id}/chat` (`reply`); drop the stored Letta token. Add the *include system status* toggle (`include_status`).
- [ ] Letta replies aren't streamed (Letta has `/messages/stream`; the dashboard doesn't stream Letta either). Add if replies get long.
- [ ] Pulling and deleting Ollama models (admin): long-running and disk-heavy, so as a job like stack actions (M4), with progress from Ollama's `/api/pull` stream.
- [ ] Rate or concurrency limit on generation: one Pi can only run a model or two at a time, and a burst of chats queues inside Ollama.

### M8: MQTT publish

- [x] `POST /api/v1/mqtt/publish` (operator) with QoS 0–2 and retain (empty retained payload clears), string or JSON payloads up to 256 KB, through the persistent MQTT connection; QoS 1/2 wait for the broker's acknowledgement (10 s). Tested against a real mosquitto (retained message read back).
- [x] Topic rules: `P4N4_API_MQTT_PUBLISH_ALLOW` (default `#`) and `_DENY` (default `sensors/#,inference/#`, deny wins), MQTT filters checked at startup. The default deny keeps operator (client) accounts from injecting readings that Node-RED would store as device data; `sandbox/...` stays open for test data. No wildcards or `$` topics. Every publish is audited.
- [ ] Per-role or per-user topic rules (e.g. admins may publish to `sensors/` to simulate a device), if the global lists turn out too coarse.
- [ ] **🖥 dashboard** A "send command" control (topic + payload), and showing `403 topic_not_allowed` clearly.

### M9: Fleet and alerts

Needed for the dashboard's *Fleet and alerts* roadmap. Scope it before starting.

- [ ] **🖥 dashboard** Deployment list served by the API instead of the dashboard's local settings (Clients tab). This is a multi-deployment concern; decide whether it belongs in each p4n4-api or in a separate fleet service.
- [ ] **🖥 dashboard** Alert rules (e.g. temperature > X, service down > N minutes) evaluated server side, with an alert feed (SSE) and incident history of status changes.
- [ ] **🖥 dashboard** Push notifications (FCM/APNs or ntfy), sent by the API rather than polled by the app.

### M10: Packaging and deployment

- [x] `Dockerfile`: multi-stage, Python 3.12 slim, non-root (UID 10001), base images pinned by digest (Dependabot), static `docker` CLI + Compose plugin from `docker:29-cli`, `p4n4-lib` from git (`P4N4_LIB` build arg), `/health` healthcheck. `docker-compose.yml` on the external `p4n4-net` as `p4n4-api`: read-only root, `cap_drop: ALL`, `no-new-privileges`, data in the `p4n4-api-data` volume, port on `127.0.0.1` only, upstream URLs pointing at the stacks' containers, `P4N4_API_TRUSTED_PROXIES=172.16.0.0/12`.
- [x] Docker access is opt-in: `P4N4_API_DOCKER=off` in the base compose file (`/stacks` → `503` so the dashboard falls back to probes; `/ready` doesn't require Docker). `docker-compose.docker.yml` mounts the socket with `group_add: DOCKER_GID`. The project is mounted read-only at its host path, so Compose's relative bind mounts resolve to the right host directories. Checked against a real IoT project: status, logs, `compose config` paths, InfluxDB/MQTT over `p4n4-net`, host metrics. `up`/`down` not run from the container yet (live stack).
  - [ ] A socket proxy (e.g. `tecnativa/docker-socket-proxy`) allowing only the calls `compose ps/up/down/restart/logs/config` and `inspect` need. Compose needs more of the API than it looks (networks, images, volumes for `up`), so test `up` with a real stack before recommending it.
  - [ ] Run `up`/`down`/`restart` from the container against a scratch stack (with a `build:` service, like the edge runner) and add it to the smoke test.
- [x] Image workflow (`.github/workflows/image.yml`): amd64 + arm64 (QEMU for the pip stage), `:edge` from main, `:X.Y.Z`/`:X.Y`/`:latest` from tags (must match `pyproject.toml` and `__version__`), smoke tests (with the socket: version, ready, bootstrap + sign-in, stacks, metrics; without: `/ready`'s docker check not required), Trivy, provenance and SBOM. Both smoke tests pass locally; the workflow itself hasn't run on GitHub yet.
- [ ] **🖥 dashboard** Once the image is published, the dashboard's nginx upstream changes from `host.docker.internal:8000` to `http://p4n4-api:8000` and `extra_hosts` goes (`dashboard/SERVICE_INTEGRATION.md` §3.1, `docker-compose.yml`).
- [x] Host-run setup for the dashboard container documented (README "Network Requirements": bind the bridge gateway, trusted proxies).
- [ ] Register an `api` layer in `p4n4-lib` and `p4n4 up --api` in the CLI. First start runs `p4n4-api users bootstrap` and shows the generated admin password (see M2). The compose file here is the starting point; the layer has to supply `P4N4_PROJECT_DIR` (the project itself) and `DOCKER_GID`.
- [ ] Publish `p4n4-lib` to PyPI (or a wheel in a release) so the image build doesn't need git and a network fetch of an unpinned `main`. Until then, release builds should pass `P4N4_LIB=...@<tag>`.

### M11: Hardening and observability

- [ ] `/metrics` (Prometheus): request counts, latency histograms, upstream call stats.
- [ ] Global rate limiting, request size limits, and timeouts on every upstream call.
- [ ] Integration tests against real containers (Mosquitto, InfluxDB, Ollama stub), in a separate CI job.
- [ ] Security notes: TLS via reverse proxy, removing the `8000` host binding in production, secret handling.
- [ ] Docs page in p4n4-docs; keep the Swagger UI descriptions and examples current.

## Open questions

- **Long-running work:** in-process background tasks are enough for one Pi. Is a job table in SQLite needed so jobs survive restarts?
- **Sync vs async:** routes are sync and run in the threadpool because `p4n4_lib.compose` uses `subprocess`. Upstream clients (InfluxDB, MQTT, HTTP) should be async. Keep both styles, or wrap `compose` in `anyio.to_thread`?
- ~~**Proxy vs re-shape** for Ollama/Letta~~ — decided in M7: p4n4-owned endpoints and request schemas, Ollama's streamed chunks passed through unchanged (the dashboard already parses them), Letta replies reshaped.
- **One API per deployment vs fleet:** the Clients tab and alerts want a cross-deployment view. Keep p4n4-api single-deployment and build fleet features elsewhere?
- **n8n:** the README doesn't plan anything for n8n. Is a thin proxy needed to trigger workflows from the CLI or dashboard?
