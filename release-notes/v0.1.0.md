First release of **p4n4-api**, the REST gateway for a p4n4 project, on port 8000.

```bash
docker pull ghcr.io/raisga/p4n4-api:0.1.0     # amd64 and arm64
```

> **Trusted networks only.** The compose file publishes port 8000 on `127.0.0.1`. To reach the API from elsewhere, put it behind a TLS reverse proxy. Mounting the Docker socket (`docker-compose.docker.yml`) gives admins control of every container on the host.

## What's in it

- **Accounts:**
  - JWT sign-in, with access tokens for 1 h and single-use refresh tokens for 7 days;
  - roles `normie`, `operator` and `admin`, plus a separate `device` role;
  - argon2id passwords and API keys;
  - rate-limited sign-in.

  `p4n4-api users dev` seeds one account per dashboard view for development.
- **Project and stacks:** the manifest (including `template` and `dashboard` blocks), validation, Compose status per stack, and admin-only stack actions (up, down, restart) run as background jobs, plus container logs.
- **Devices and telemetry:** a device registry with per-device API keys; batch ingest into InfluxDB plus MQTT; queries with windowed aggregates; live readings over server-sent events.
- **Edge:** the runner's backend and model, classification of feature vectors, stored inference results, and host metrics (CPU, memory, disk, temperature).
- **AI:** Ollama models, chat and generation (streamed), and Letta agents, with Letta's password kept server-side.
- **MQTT publish** to allowed topics, and an **audit log** of stack actions and user and device changes.
- OpenAPI at `/openapi.json`, Swagger UI at `/swagger-ui`.

## Compatibility

- Built on **p4n4-lib 0.2.0**.
- Pairs with **p4n4-dashboard 1.1.0**, whose normie view needs this release's `normie` role.

## Limitations

- Not yet implemented: Prometheus metrics (`/metrics`). See the README's design-target notes.
