"""Tests for the p4n4-api endpoints."""

from __future__ import annotations

from p4n4_lib import env as envutil
from p4n4_lib import manifest as mf

from p4n4_api.docker import daemon_error as real_daemon_error  # before docker_up replaces it

# ── /health ───────────────────────────────────────────────────────────────────


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── /api/v1/project ───────────────────────────────────────────────────────────


def test_project_requires_manifest(client, tmp_path, monkeypatch):
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    r = client.get("/api/v1/project")
    assert r.status_code == 404
    assert ".p4n4.json" in r.json()["error"]["message"]


def test_project_info_flat(client, flat_project):
    r = client.get("/api/v1/project")
    assert r.status_code == 200
    body = r.json()
    assert body["project"] == "proj"
    assert body["layers"] == ["iot"]
    assert body["layout"] == "flat"
    assert body["stacks"] == [{"name": "iot", "dir": str(flat_project), "relative_dir": "."}]
    assert body["template"] is None
    assert body["dashboard"] is None


def test_project_info_template_and_dashboard(client, flat_project):
    path = flat_project / mf.MANIFEST_FILE
    data = mf.load(path)
    data["template"] = {"name": "mqtt-influx-grafana", "version": "0.2.0"}
    data["dashboard"] = {
        "grafana_path": "/d/p4n4-telemetry/telemetry",
        "tabs": ["services", "grafana"],
    }
    mf.save(path, data)
    body = client.get("/api/v1/project").json()
    assert body["template"] == data["template"]
    assert body["dashboard"] == data["dashboard"]


def test_project_info_multi(client, multi_project):
    r = client.get("/api/v1/project")
    assert r.status_code == 200
    body = r.json()
    assert body["layers"] == ["iot", "ai"]
    assert body["layout"] == "multi"
    assert [s["name"] for s in body["stacks"]] == ["iot", "ai"]
    assert [s["relative_dir"] for s in body["stacks"]] == ["iot", "ai"]


# ── /api/v1/project/validate ──────────────────────────────────────────────────


def test_validate_passes(client, multi_project):
    r = client.get("/api/v1/project/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["errors"] == []
    assert "iot/.env: all required keys present" in body["passed"]


def test_validate_reports_errors(client, multi_project):
    (multi_project / "ai" / "config/letta/letta.conf").unlink()
    env_path = multi_project / "iot" / ".env"
    env = envutil.load(env_path)
    del env["GRAFANA_PASSWORD"]
    envutil.write(env_path, env)

    r = client.get("/api/v1/project/validate")
    body = r.json()
    assert body["ok"] is False
    assert "Missing file: ai/config/letta/letta.conf" in body["errors"]
    assert "iot/.env missing required key: GRAFANA_PASSWORD" in body["errors"]


def test_validate_reports_dashboard_errors(client, flat_project):
    path = flat_project / mf.MANIFEST_FILE
    data = mf.load(path)
    data["dashboard"] = {"grafana_path": "d/no-slash", "colour": "red"}
    mf.save(path, data)

    body = client.get("/api/v1/project/validate").json()
    assert body["ok"] is False
    assert '.p4n4.json: dashboard.grafana_path must be a path starting with "/"' in body["errors"]
    assert ".p4n4.json: dashboard.colour is not a known setting" in body["errors"]


# ── /api/v1/stacks ────────────────────────────────────────────────────────────


def _fake_ps(services_by_dir):
    def ps(cwd):
        return services_by_dir.get(cwd.name, [])

    return ps


def test_stacks_multi(client, multi_project, monkeypatch):
    monkeypatch.setattr(
        "p4n4_api.routes.stacks.compose.ps",
        _fake_ps(
            {
                "iot": [
                    {"Service": "mosquitto", "State": "running", "Health": "healthy"},
                    {"Service": "influxdb", "State": "exited", "Health": ""},
                ],
                "ai": [],
            }
        ),
    )
    r = client.get("/api/v1/stacks")
    assert r.status_code == 200
    stacks = {s["name"]: s for s in r.json()["stacks"]}
    assert stacks["iot"]["running"] == 1
    assert stacks["iot"]["total"] == 2
    assert stacks["ai"]["services"] == []


def test_single_stack(client, multi_project, monkeypatch):
    monkeypatch.setattr(
        "p4n4_api.routes.stacks.compose.ps",
        _fake_ps({"ai": [{"Service": "ollama", "State": "running", "Health": ""}]}),
    )
    r = client.get("/api/v1/stacks/ai")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "ai"
    assert body["services"][0]["name"] == "ollama"


def test_stack_status_503_when_compose_fails(client, multi_project, monkeypatch):
    from p4n4_lib import compose

    def ps(cwd):
        raise compose.DockerError("`docker compose ps` failed: invalid compose file")

    monkeypatch.setattr("p4n4_api.routes.stacks.compose.ps", ps)
    r = client.get("/api/v1/stacks/ai")
    assert r.status_code == 503
    assert "invalid compose file" in r.json()["error"]["message"]


def test_unknown_stack_404(client, multi_project):
    r = client.get("/api/v1/stacks/nope")
    assert r.status_code == 404


def test_stacks_503_when_docker_down(client, multi_project, monkeypatch):
    # The dashboard treats any non-200 as "API down" and falls back to port probes.
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: "cannot connect")
    for path in ("/api/v1/stacks", "/api/v1/stacks/iot"):
        r = client.get(path)
        assert r.status_code == 503
        assert "cannot connect" in r.json()["error"]["message"]


def test_docker_off(client, multi_project, monkeypatch):
    # A container without the Docker socket: status falls back, /ready stays ready.
    monkeypatch.setenv("P4N4_API_DOCKER", "off")
    monkeypatch.setattr("p4n4_api.docker.daemon_error", real_daemon_error)
    r = client.get("/api/v1/stacks")
    assert r.status_code == 503
    assert "P4N4_API_DOCKER=off" in r.json()["error"]["message"]
    # Without the iot layer, nothing else /ready requires is missing in tests.
    mf.save(multi_project / mf.MANIFEST_FILE, mf.create("ai-only", ["ai"]))
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["docker"] == {
        "ok": False,
        "detail": "Docker access is disabled (P4N4_API_DOCKER=off).",
        "required": False,
    }


def test_stack_service_details(client, multi_project, monkeypatch):
    from datetime import UTC, datetime, timedelta

    full_id = "abc123" + "0" * 58
    started = datetime.now(UTC) - timedelta(hours=2)
    monkeypatch.setattr("p4n4_api.docker.started_at", lambda ids: {full_id: started})
    publishers = [
        {"URL": "0.0.0.0", "TargetPort": 1883, "PublishedPort": 1883, "Protocol": "tcp"},
        {"URL": "::", "TargetPort": 1883, "PublishedPort": 1883, "Protocol": "tcp"},
        {"URL": "", "TargetPort": 9001, "PublishedPort": 0, "Protocol": "tcp"},
    ]
    monkeypatch.setattr(
        "p4n4_api.routes.stacks.compose.ps",
        _fake_ps(
            {
                "iot": [
                    {
                        "ID": "abc123",
                        "Service": "mosquitto",
                        "State": "running",
                        "Health": "healthy",
                        "Image": "eclipse-mosquitto:2.0.18",
                        "Status": "Up 2 hours (healthy)",
                        "ExitCode": 0,
                        "Publishers": publishers,
                    },
                    {
                        "ID": "def456",
                        "Service": "influxdb",
                        "State": "exited",
                        "Image": "influxdb@sha256:" + "f" * 64,
                        "Status": "Exited (1) 3 minutes ago",
                        "ExitCode": 1,
                    },
                ]
            }
        ),
    )
    services = {s["name"]: s for s in client.get("/api/v1/stacks/iot").json()["services"]}

    mqtt = services["mosquitto"]
    assert mqtt["image"] == "eclipse-mosquitto:2.0.18"
    assert mqtt["version"] == "2.0.18"
    assert mqtt["status"] == "Up 2 hours (healthy)"
    assert mqtt["exit_code"] is None
    assert mqtt["ports"] == [{"published": 1883, "target": 1883, "protocol": "tcp"}]
    assert 7190 <= mqtt["uptime_s"] <= 7210
    assert mqtt["started_at"] == started.isoformat()

    influx = services["influxdb"]
    assert influx["version"] is None
    assert influx["exit_code"] == 1
    assert influx["uptime_s"] is None and influx["started_at"] is None
    assert influx["ports"] == []


def test_stack_legacy_compose_fields(client, multi_project, monkeypatch):
    # docker-compose v1 output (built from docker inspect) has no Image/Status/ID.
    monkeypatch.setattr(
        "p4n4_api.routes.stacks.compose.ps",
        _fake_ps({"ai": [{"Name": "p4n4-ollama", "Service": "ollama", "State": "running"}]}),
    )
    svc = client.get("/api/v1/stacks/ai").json()["services"][0]
    assert svc["name"] == "ollama"
    assert svc["image"] is None and svc["version"] is None and svc["uptime_s"] is None


def test_stack_null_fields_from_compose(client, multi_project, monkeypatch):
    # Some Compose versions print null rather than leaving a field out.
    monkeypatch.setattr(
        "p4n4_api.routes.stacks.compose.ps",
        _fake_ps({"ai": [{"Service": "ollama", "State": None, "Health": None, "ExitCode": None}]}),
    )
    r = client.get("/api/v1/stacks/ai")
    assert r.status_code == 200
    svc = r.json()["services"][0]
    assert (svc["state"], svc["health"]) == ("?", "")


def test_image_version():
    from p4n4_api.routes.stacks import _image_version

    assert _image_version("influxdb:2.7") == "2.7"
    assert _image_version("ghcr.io/raisga/p4n4-dashboard:1.0.0") == "1.0.0"
    assert _image_version("localhost:5000/runner") is None
    assert _image_version("localhost:5000/runner:v3") == "v3"
    assert _image_version("ollama/ollama") is None
    assert _image_version("influxdb:2.7@sha256:" + "f" * 64) == "2.7"


# ── /ready, /api/v1/version ───────────────────────────────────────────────────


def test_ready(client, multi_project):
    r = client.get("/ready")
    assert r.json()["checks"]["project"] == {"ok": True}
    assert r.json()["checks"]["docker"] == {"ok": True}


def test_ready_without_iot_layer(client, tmp_path, monkeypatch):
    from p4n4_lib import manifest as mf

    mf.save(tmp_path / mf.MANIFEST_FILE, mf.create("ai-only", ["ai"]))
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    r = client.get("/ready")
    assert r.status_code == 200  # Ollama and Letta are reported, not required
    checks = r.json()["checks"]
    assert "influxdb" not in checks and "mqtt" not in checks
    assert checks["ollama"]["required"] is False and checks["letta"]["ok"] is False


def test_ready_checks_influxdb_and_reports_mqtt(client, flat_project, influxdb):
    import httpx

    influxdb.handler = lambda request: httpx.Response(200, json={"status": "pass"})
    r = client.get("/ready")
    assert r.status_code == 200  # MQTT is down, but it isn't required
    checks = r.json()["checks"]
    assert checks["influxdb"] == {"ok": True}
    assert checks["mqtt"] == {"ok": False, "detail": "not started", "required": False}
    assert str(influxdb.requests[0].url) == "http://localhost:8086/health"

    influxdb.handler = lambda request: httpx.Response(503)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["influxdb"] == {"ok": False, "detail": "health check returned 503"}


def test_ready_reports_failures(client, tmp_path, monkeypatch):
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: "cannot connect")
    r = client.get("/ready")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["docker"] == {"ok": False, "detail": "cannot connect"}
    assert checks["project"]["ok"] is False
    assert ".p4n4.json" in checks["project"]["detail"]


def test_version(client):
    from p4n4_api import __version__

    body = client.get("/api/v1/version").json()
    assert body["version"] == __version__
    assert body["api"] == "v1"
    assert body["p4n4_lib"]


# ── CORS ──────────────────────────────────────────────────────────────────────


def _preflight(client, origin):
    return client.options(
        "/api/v1/stacks",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_cors_off_by_default(client):
    r = client.get("/health", headers={"Origin": "http://localhost:8088"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_allowlist(monkeypatch):
    from fastapi.testclient import TestClient

    from p4n4_api.main import create_app

    monkeypatch.setenv("P4N4_API_CORS_ORIGINS", "http://localhost:8088, http://pi.local:8088/")
    client = TestClient(create_app())

    r = _preflight(client, "http://pi.local:8088")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://pi.local:8088"
    assert "access-control-allow-credentials" not in r.headers

    r = client.get("/health", headers={"Origin": "http://localhost:8088"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:8088"

    assert _preflight(client, "http://evil.example").status_code == 400
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# ── /api/v1/edge/metrics ──────────────────────────────────────────────────────


def _sensor(current):
    from types import SimpleNamespace

    return SimpleNamespace(label="", current=current, high=None, critical=None)


def test_edge_metrics_contract(client, tmp_path, monkeypatch):
    # Host-level: works without a p4n4 project.
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    r = client.get("/api/v1/edge/metrics")
    assert r.status_code == 200
    body = r.json()
    for key in ("cpu_percent", "mem_percent", "mem_used_mb", "mem_total_mb", "disk_percent"):
        assert isinstance(body[key], int | float)
    assert 0 <= body["cpu_percent"] <= 100
    assert 0 <= body["mem_percent"] <= 100
    assert body["mem_used_mb"] <= body["mem_total_mb"]
    assert body["uptime_s"] > 0
    assert len(body["load"]) == 3
    assert None not in body.values()


def test_edge_metrics_disk_path(client, tmp_path, monkeypatch):
    from p4n4_api.routes import edge

    seen = []
    real_disk_usage = edge.psutil.disk_usage
    usage = type("Usage", (), {"percent": 42.0})
    monkeypatch.setattr(edge.psutil, "disk_usage", lambda path: seen.append(path) or usage)
    monkeypatch.setenv("P4N4_API_DISK_PATH", str(tmp_path))
    assert client.get("/api/v1/edge/metrics").json()["disk_percent"] == 42.0
    assert seen == [str(tmp_path)]

    # A path that doesn't exist (volume not mounted) omits the field instead of failing.
    monkeypatch.setattr(edge.psutil, "disk_usage", real_disk_usage)
    monkeypatch.setenv("P4N4_API_DISK_PATH", str(tmp_path / "missing"))
    r = client.get("/api/v1/edge/metrics")
    assert r.status_code == 200 and "disk_percent" not in r.json()


def test_edge_metrics_prefers_cpu_sensor(client, monkeypatch):
    from p4n4_api.routes import edge

    sensors = {"acpitz": [_sensor(25.0)], "nvme": [_sensor(30.0)], "coretemp": [_sensor(45.04)]}
    monkeypatch.setattr(edge.psutil, "sensors_temperatures", lambda: sensors, raising=False)
    assert client.get("/api/v1/edge/metrics").json()["temp_c"] == 45.0


def test_edge_metrics_matches_unknown_cpu_sensor(client, monkeypatch):
    from p4n4_api.routes import edge

    sensors = {"acpitz": [_sensor(25.0)], "rockchip_cpu": [], "a53_cpu_thermal": [_sensor(52.3)]}
    monkeypatch.setattr(edge.psutil, "sensors_temperatures", lambda: sensors, raising=False)
    assert client.get("/api/v1/edge/metrics").json()["temp_c"] == 52.3


def test_edge_metrics_omits_unknown_temp(client, monkeypatch):
    from p4n4_api.routes import edge

    monkeypatch.setattr(
        edge.psutil, "sensors_temperatures", lambda: {"acpitz": [_sensor(25.0)]}, raising=False
    )
    assert "temp_c" not in client.get("/api/v1/edge/metrics").json()

    # macOS / Windows: psutil has no sensors_temperatures at all.
    monkeypatch.delattr(edge.psutil, "sensors_temperatures", raising=False)
    assert "temp_c" not in client.get("/api/v1/edge/metrics").json()


# ── OpenAPI schemas ───────────────────────────────────────────────────────────


def _schemas(c) -> tuple[dict, dict]:
    spec = c.get("/openapi.json").json()
    return spec["paths"], spec["components"]["schemas"]


def test_every_endpoint_documents_its_response(anon):
    paths, _ = _schemas(anon)
    untyped = []
    for path, ops in paths.items():
        for method, op in ops.items():
            ok = next((op["responses"][s] for s in ("200", "201") if s in op["responses"]), None)
            if ok is None:
                continue  # 204: no body
            content = ok.get("content", {})
            if set(content) == {"text/event-stream"}:
                continue  # a stream: its events are documented in the description
            schema = content.get("application/json", {}).get("schema", {})
            if not schema or schema.get("additionalProperties") is True:
                untyped.append(f"{method.upper()} {path}")
    assert untyped == []


def test_dashboard_contracts_in_schema(anon):
    # p4n4-dashboard parses these; changing them breaks it, so this test should too.
    paths, schemas = _schemas(anon)
    assert {"name", "state", "health"} <= set(schemas["Service"]["required"])
    assert {"services", "running", "total"} <= set(schemas["Stack"]["required"])
    assert set(schemas["EdgeMetrics"]["required"]) == {"cpu_percent", "mem_percent"}
    ready = paths["/ready"]["get"]["responses"]
    assert ready["503"]["content"]["application/json"]["schema"]["$ref"].endswith("/Readiness")
