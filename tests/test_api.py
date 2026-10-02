"""Tests for the p4n4-api endpoints."""

from __future__ import annotations

from p4n4_lib import env as envutil

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
    assert ".p4n4.json" in r.json()["detail"]


def test_project_info_flat(client, flat_project):
    r = client.get("/api/v1/project")
    assert r.status_code == 200
    body = r.json()
    assert body["project"] == "proj"
    assert body["layers"] == ["iot"]
    assert body["layout"] == "flat"
    assert body["stacks"] == [{"name": "iot", "dir": str(flat_project), "relative_dir": "."}]


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


def test_unknown_stack_404(client, multi_project):
    r = client.get("/api/v1/stacks/nope")
    assert r.status_code == 404


def test_stacks_503_when_docker_down(client, multi_project, monkeypatch):
    # The dashboard treats any non-200 as "API down" and falls back to port probes.
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: "cannot connect")
    for path in ("/api/v1/stacks", "/api/v1/stacks/iot"):
        r = client.get(path)
        assert r.status_code == 503
        assert "cannot connect" in r.json()["detail"]


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


def test_image_version():
    from p4n4_api.routes.stacks import _image_version

    assert _image_version("influxdb:2.7") == "2.7"
    assert _image_version("ghcr.io/raisga/p4n4-dashboard:1.0.0") == "1.0.0"
    assert _image_version("localhost:5000/runner") is None
    assert _image_version("localhost:5000/runner:v3") == "v3"
    assert _image_version("ollama/ollama") is None
    assert _image_version("influxdb:2.7@sha256:" + "f" * 64) == "2.7"


# ── /ready, /api/v1/version ───────────────────────────────────────────────────


def test_ready(client, flat_project):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ready",
        "checks": {"project": {"ok": True}, "docker": {"ok": True}},
    }


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
