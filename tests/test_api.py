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
