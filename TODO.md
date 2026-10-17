# TODO

_Last updated: 2026-10-16 (v0.3.0)_

Pending work for `p4n4-api`. The [README](README.md) describes the target surface. This file tracks what's built, what's missing, and the order to build it in. The main consumer is [`p4n4-dashboard`](../dashboard/), so items it's waiting on are marked **🖥 dashboard**.

## Current status (v0.3.0)

| Area | Status | Notes |
|------|--------|-------|
| Project info / validation | ✅ Working | `GET /api/v1/project`, `GET /api/v1/project/validate`, built on `p4n4-lib` (flat and multi-layer layouts, the legacy edge layer included). |
| Dashboard content | ✅ Working | `GET /api/v1/project/home` (the project's `HOME.md`, per language), `GET /api/v1/project/vision` (the vision overlay `dashboard.vision` names), `GET /api/v1/feed` and item actions (relays `dashboard.feed`), `/api/v1/chats` (relays `dashboard.chats`). Upstream tokens come from the layer `.env` and never reach a browser. |
| Dashboard settings | ✅ Working | `/api/v1/dashboard/views` (tabs and order per view), `/view-settings` (start tab, Home cards, Grafana page, cameras, overlay, alerts) and `/banner`; admins write, everyone signed in reads, writes are audited. |
| Stack status | ✅ Working | `GET /api/v1/stacks[/{stack}]` from `docker compose ps` per stack, with image, version, ports and uptime per service, and `503` when Docker is unreachable. |
| Health | ✅ Working | `/health`, `/ready` (project, Docker, InfluxDB; MQTT, the runner, Ollama and Letta reported), `/api/v1/version`. |
| Auth | ✅ Working | People: username/password → JWT, `normie`/`operator`/`admin` roles, refresh rotation, sign-out that ends access tokens at once, `must_change_password`, admin sign-out everywhere, `last_login_at`; `p4n4-api users` CLI and `/api/v1/users`. Devices: API key → JWT, `device` role. The dashboard signs in and has the Users screen and password form. |
| Device registry | ✅ Working | `/api/v1/devices` CRUD, paginated, with API key rotation (M3). No dashboard screen yet. |
| Stack control | ✅ Working | Up/down/restart (stack, all, or one service) as background jobs, container logs with SSE follow, audit log (M4). The dashboard's stack-controls menu uses the jobs; no logs viewer yet. |
| Telemetry | ✅ Working | Device ingest to InfluxDB (+ marked MQTT publish), Flux queries, live SSE stream from MQTT (M5). Not used by the dashboard yet. |
| Upstream proxies | ✅ Working | InfluxDB, MQTT (M5), the inference runner (M6: the ai layer's `ei-runner`, or a legacy edge layer), Ollama and Letta (M7). |
| MQTT publish | ✅ Working | `POST /api/v1/mqtt/publish` with QoS/retain, topic allow/deny lists, audited (M8). |
| AI agents | ✅ Working | Ollama models, streamed chat/generate, Letta agents, `include_status`, and the deployment's chosen assistant (`/api/v1/agents/config`; normies may only use it) (M7). The dashboard's Assistant goes through these. |
| Edge metrics | ✅ Working | `GET /api/v1/edge/metrics` via `psutil`, in the dashboard's contract, with the runner's `inference_ms` (M6). |
| Inference | ✅ Working | The runner (Edge Impulse, ONNX or mock backend): model info, on-demand inference, stored results (M6). |
| CORS | ✅ Working | `P4N4_API_CORS_ORIGINS` allowlist; off by default. |
| Packaging | ✅ Published | `ghcr.io/raisga/p4n4-api` 0.1.0–0.3.0 (amd64, arm64, non-root) from `image.yml`; installs p4n4-lib from PyPI. `docker-compose.yml` on `p4n4-net`; Docker socket opt-in (M10). Not yet a p4n4 layer in the CLI. |
| Tests / CI | ✅ Working | 463 tests pass (2026-10-17) with synthetic projects, a stubbed Compose client for status and a fake `docker compose` script for control and logs. CI runs ruff and pytest on 3.11–3.13. |

## Housekeeping

- [x] Response models (Pydantic) for every endpoint; tests pin the dashboard's contracts and fail on an untyped body.
- [x] Settings on `pydantic-settings` (`p4n4_api/config.py`): typed and validated at startup.
- [x] Standard error body (`{"error": {"code", "message"}}`, plus `fields` for validation) for every 4xx/5xx.
  - [x] Unhandled `500`s now come from our own middleware (`errors.UnhandledErrorMiddleware`), inside CORS and the request-ID middleware, so they carry CORS headers and `X-Request-ID`. The exception is still re-raised, so uvicorn logs the traceback (without the request ID; the access line has it).
- [x] `compose.ps` failures (Docker CLI missing, daemon down, Compose missing) return `503` from `/stacks`.
  - [x] Root cause in `p4n4-lib`: `compose.ps` now raises `DockerError` when Compose fails.
  - [x] Status reads (`/stacks`, `include_status`) no longer run `docker version` first; they rely on `compose.ps` raising and only check `P4N4_API_DOCKER`. `/ready` and stack actions still probe the daemon.
  - [ ] `compose.ps` has no timeout, so a hung daemon now holds a `/stacks` request (the probe gave up after 5 s). Add a timeout in `p4n4-lib`'s `_ps_run` that raises `DockerError`.
- [x] Structured logging with a request ID (`X-Request-ID` in and out), `P4N4_API_LOG_FORMAT=text|json`, `P4N4_API_LOG_LEVEL`.
  - [ ] **🖥 dashboard** Send `X-Request-ID` on API calls and show it in error messages, so a report can be matched to the server log.

## Milestones

In dependency order. Each one should ship with tests, README updates and a CHANGELOG entry. Done items are summarised; the README documents them.

### M1: Dashboard unblockers — done

Edge metrics, CORS, richer stack status, `/ready`, `/api/v1/version`.

- [ ] Check `temp_c` on a real Raspberry Pi 5 and on a Jetson, and add their sensor names if they're missing.
- [ ] **🖥 dashboard** Show the richer stack status (image, version, uptime) in Services (its `ComposeService` ignores unknown fields, so nothing breaks meanwhile).

### M2: Auth and roles — done

JWTs, people and device accounts, the `normie`/`operator`/`admin` ranking, refresh rotation and reuse detection, sign-out that ends access tokens at once (`sid`), rate-limited sign-in, `P4N4_API_TRUSTED_PROXIES`, user management, self-service password change, `must_change_password`, admin sign-out everywhere, `users bootstrap` and `users dev`. The dashboard has sign-in, the Users screen and the password form (1.3.0).

- [ ] `p4n4 init` / `p4n4 up` creates the first admin (`p4n4-api users bootstrap` in the API's container) and shows its password once. Blocked on the `api` layer (M10).
- [ ] No per-username rate limit, on purpose (it would let anyone lock out `admin`). Revisit only with a lockout-free design if distributed guessing shows up.
- [ ] `token_gen` is redundant with `sid`; drop it in a later migration if the schema is touched anyway.

### M3: Device registry — done

Devices table, `device` role, CRUD, key rotation, argon2id-hashed `p4n4_…` keys; `last_seen_at` also updates on telemetry ingest (throttled, M5).

- [ ] Many devices behind one NAT share the sign-in rate limit (~600 key exchanges an hour). Give key exchange its own, larger bucket if fleets grow.
- [ ] `p4n4-api devices` CLI (list/add/rotate/remove), like `users`, for setups without the dashboard. Optional: curl works.
- [ ] **🖥 dashboard** Devices screen: list with `last_seen_at`, register (show the key once with a copy button), rotate, disable, remove.

### M4: Stack control and logs — done

Jobs for up/down/restart (stack, `all`, one service), container logs with SSE follow, audit log. The dashboard's stack-controls menu runs and waits for the jobs.

- [ ] `p4n4_lib.compose.up/down/logs` print to the terminal and return only an exit code, so the API builds Compose's arguments itself (`jobs._commands`, `control._logs_cmd`). Move argument builders (or output-capturing variants) into `p4n4-lib` so the CLI and API can't drift.
- [ ] Jobs are in memory. If an API restart mid-`up` matters, persist them in SQLite and mark running ones `interrupted` at startup.
- [ ] **🖥 dashboard** Logs viewer using the SSE stream (`EventSource` can't send `Authorization`, so read the stream with `fetch`), and the job's output live.

### M5: Telemetry — done

Async InfluxDB client, device ingest with Node-RED's schema (the IoT stack's flow skips the API's marked messages), Flux queries, the MQTT connection and the live SSE stream.

- [ ] **🖥 dashboard** Live sensor values for Home and Edge from the stream (read with `fetch`), and history charts from `GET /telemetry`.
- [ ] Inference results (`inference/{device}/result`) and the `sandbox/` topics aren't in the live stream (it subscribes to `sensors/+/+` only).
- [ ] Batches are written in one request; InfluxDB rejects the whole batch on one bad point (`422 influxdb_rejected`). If partial success is needed, retry point by point and report which failed.
- [ ] **🖥 dashboard** Metrics history for longer Edge ranges and CSV export: store `edge/metrics` samples in InfluxDB, or read them from an existing exporter.

### M6: Inference — done

Runner client for every backend, `GET /inference/runner`, `POST /inference` (with `wrong_feature_count` and mock-result checks), stored results, `inference_ms` in edge metrics. Follows the runner to the ai layer (`ei-runner`) or a legacy edge layer.

- [ ] **🖥 dashboard** Edge tab: runner backend/model card, a "classify" form and recent results.

### M7: AI agents — done

Ollama models, streamed chat/generate, Letta agents (password kept server side), `include_status`, the assistant config. The dashboard uses all of it and no longer stores a Letta credential.

- [ ] Letta replies aren't streamed (Letta has `/messages/stream`). Add if replies get long.
- [ ] Pulling and deleting Ollama models (admin), as a job like stack actions, with progress from Ollama's `/api/pull` stream.
- [ ] Rate or concurrency limit on generation: a Pi runs a model or two at a time, and a burst of chats queues inside Ollama.

### M8: MQTT publish — done

- [ ] Per-role or per-user topic rules (e.g. admins may publish to `sensors/` to simulate a device), if the global allow/deny lists turn out too coarse.
- [ ] **🖥 dashboard** A "send command" control (topic + payload), and showing `403 topic_not_allowed` clearly.

### M9: Fleet and alerts

Needed for the dashboard's *Fleet and alerts* roadmap. Scope it before starting.

- [ ] **🖥 dashboard** Deployment list served by the API instead of the dashboard's local settings (Clients tab). Decide whether it belongs in each p4n4-api or in a separate fleet service.
- [ ] **🖥 dashboard** Alert rules (e.g. temperature > X, service down > N minutes) evaluated server side, with an alert feed (SSE) and incident history of status changes. The Feed relay covers alerts a project's own service raises; this is for rules p4n4 evaluates itself.
- [ ] **🖥 dashboard** Push notifications (FCM/APNs or ntfy), sent by the API rather than polled by the app.

### M10: Packaging and deployment

Done: the multi-stage, non-root image (pinned base, static Docker CLI and Compose, p4n4-lib from PyPI); the compose file on `p4n4-net` with a read-only root and no capabilities; Docker access opt-in (`docker-compose.docker.yml`); the image workflow (amd64 + arm64, smoke tests, Trivy, provenance, SBOM), published for 0.1.0–0.3.0.

- [ ] A socket proxy (e.g. `tecnativa/docker-socket-proxy`) allowing only what `compose ps/up/down/restart/logs/config` and `inspect` need. Test `up` with a real stack before recommending it.
- [ ] Run `up`/`down`/`restart` from the container against a scratch stack (with a `build:` service, like the runner) and add it to the smoke test.
- [ ] **🖥 dashboard** The dashboard's nginx upstream still defaults to `host.docker.internal:8000`; switch the default to `http://p4n4-api:8000` and drop `extra_hosts` once the API runs as a layer.
- [ ] Register an `api` layer in `p4n4-lib` and start it from the CLI (`p4n4 up`). First start runs `p4n4-api users bootstrap` and shows the generated admin password (M2). The compose file here is the starting point; the layer has to supply `P4N4_PROJECT_DIR` and `DOCKER_GID`.

### M11: Hardening and observability

- [ ] `/metrics` (Prometheus): request counts, latency histograms, upstream call stats.
- [ ] Global rate limiting, request size limits, and timeouts on every upstream call.
- [ ] Integration tests against real containers (Mosquitto, InfluxDB, Ollama stub), in a separate CI job.
- [ ] Security notes: TLS via reverse proxy, removing the `8000` host binding in production, secret handling.
- [x] Docs page in p4n4-docs (`reference/api.md`). Keep it and the Swagger UI descriptions current.

## Open questions

- **Long-running work:** in-process background tasks are enough for one Pi. Is a job table in SQLite needed so jobs survive restarts?
- **Sync vs async:** routes that shell out to Compose are sync and run in the threadpool; upstream clients are async. Keep both styles, or wrap `compose` in `anyio.to_thread`?
- **One API per deployment vs fleet:** the Clients tab and alerts want a cross-deployment view. Keep p4n4-api single-deployment and build fleet features elsewhere?
- **n8n:** nothing is planned for n8n. Is a thin proxy needed to trigger workflows from the CLI or dashboard?
- ~~**Proxy vs re-shape** for Ollama/Letta~~ — decided in M7.
- ~~**A read-only viewer role**~~ — decided: the `normie` role.
