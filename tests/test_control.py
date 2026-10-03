"""Tests for stack control jobs, logs and the audit log, against a fake `docker compose`."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from p4n4_api import jobs
from p4n4_api.routes import control

FAKE = Path(__file__).with_name("fake_compose.py")


@pytest.fixture(autouse=True)
def fresh_jobs():
    jobs.reset()
    yield
    jobs.reset()


@pytest.fixture()
def calls(tmp_path_factory, monkeypatch):
    """Route Compose to tests/fake_compose.py; returns a reader for the calls it got."""
    log = tmp_path_factory.mktemp("compose") / "calls.jsonl"
    log.touch()
    monkeypatch.setenv("FAKE_COMPOSE_LOG", str(log))
    monkeypatch.setattr("p4n4_lib.compose.compose_cmd", lambda: (sys.executable, str(FAKE)))

    def read() -> list[tuple[str, str]]:
        lines = log.read_text().splitlines()
        return [(" ".join(c["args"]), c["cwd"]) for c in map(json.loads, lines)]

    return read


def _wait(c, job_id: str, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} still {job['status']}")


def _audit(admin) -> list[tuple[str, str, str]]:
    items = admin.get("/api/v1/audit").json()["items"]
    return [(e["action"], e["target"], e["outcome"]) for e in reversed(items)]


# ── Stack actions ─────────────────────────────────────────────────────────────


def test_up_runs_as_a_job(admin, multi_project, calls):
    r = admin.post("/api/v1/stacks/iot/up")
    assert r.status_code == 202
    job = r.json()
    assert r.headers["Location"] == f"/api/v1/jobs/{job['id']}"
    assert (job["action"], job["stack"], job["requested_by"]) == ("up", "iot", "root")

    done = _wait(admin, job["id"])
    assert (done["status"], done["exit_code"]) == ("succeeded", 0)
    assert done["output"][-1] == "up -d done in iot"
    assert done["output"][0].startswith("$ ")
    assert calls() == [("up -d", "iot")]


def test_pull(admin, multi_project, calls):
    _wait(admin, admin.post("/api/v1/stacks/ai/up", params={"pull": True}).json()["id"])
    assert calls() == [("up -d --pull=always", "ai")]
    assert admin.post("/api/v1/stacks/ai/down", params={"pull": True}).status_code == 422


def test_pull_on_legacy_compose(monkeypatch):
    monkeypatch.setattr("p4n4_lib.compose.compose_cmd", lambda: ("docker-compose",))
    assert jobs._commands("up", None, True) == [["pull"], ["up", "-d"]]
    assert jobs._commands("down", None, False) == [["down"]]  # never -v


def test_all_stacks_in_dependency_order(admin, multi_project, calls):
    _wait(admin, admin.post("/api/v1/stacks/all/up").json()["id"])
    _wait(admin, admin.post("/api/v1/stacks/all/down").json()["id"])
    assert calls() == [("up -d", "iot"), ("up -d", "ai"), ("down", "ai"), ("down", "iot")]


def test_failure_stops_later_stacks(admin, multi_project, calls, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "iot")
    done = _wait(admin, admin.post("/api/v1/stacks/all/up").json()["id"])
    assert (done["status"], done["exit_code"]) == ("failed", 1)
    assert "failing in iot" in done["output"]
    assert [cwd for _, cwd in calls()] == ["iot"]


def test_timeout_kills_compose(admin, multi_project, calls, monkeypatch):
    monkeypatch.setattr(jobs, "TIMEOUT_S", 0.3)
    monkeypatch.setenv("FAKE_SLEEP", "5")
    done = _wait(admin, admin.post("/api/v1/stacks/iot/restart").json()["id"])
    assert done["status"] == "failed"
    assert done["output"][-1] == "Timed out after 0.3 s; stopped."


def test_identical_queued_job_is_reused(admin, multi_project, calls, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "0.5")
    running = admin.post("/api/v1/stacks/iot/restart").json()
    deadline = time.monotonic() + 5
    while admin.get(f"/api/v1/jobs/{running['id']}").json()["status"] == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    queued = admin.post("/api/v1/stacks/iot/restart").json()
    again = admin.post("/api/v1/stacks/iot/restart").json()
    assert queued["id"] != running["id"]
    assert again["id"] == queued["id"]  # double click
    _wait(admin, queued["id"])
    assert len(calls()) == 2


def test_jobs_list_newest_first_without_output(admin, client, multi_project, calls):
    first = admin.post("/api/v1/stacks/iot/up").json()["id"]
    second = admin.post("/api/v1/stacks/ai/up").json()["id"]
    _wait(admin, second)
    listed = client.get("/api/v1/jobs").json()  # operators can follow progress
    assert [j["id"] for j in listed] == [second, first]
    assert all(j["output"] == [] for j in listed)
    assert client.get("/api/v1/jobs/nope").status_code == 404


def test_unknown_stack_404(admin, multi_project, calls):
    assert admin.post("/api/v1/stacks/nope/up").status_code == 404
    assert admin.post("/api/v1/stacks/iot/explode").status_code == 422
    assert calls() == []


def test_control_needs_admin(client, multi_project, calls):
    assert client.post("/api/v1/stacks/iot/up").status_code == 403
    assert client.post("/api/v1/stacks/iot/services/web/restart").status_code == 403
    assert client.get("/api/v1/stacks/iot/logs").status_code == 403
    assert calls() == []


def test_docker_down_503(admin, multi_project, calls, monkeypatch):
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: "cannot connect")
    assert admin.post("/api/v1/stacks/iot/up").status_code == 503


def test_restart_one_service(admin, multi_project, calls):
    done = _wait(admin, admin.post("/api/v1/stacks/iot/services/web/restart").json()["id"])
    assert (done["service"], done["status"]) == ("web", "succeeded")
    assert calls() == [("config --services", "iot"), ("restart web", "iot")]


@pytest.mark.parametrize("service", ["nope", "-rf", "web;ls"])
def test_restart_unknown_service_404(admin, multi_project, calls, service):
    r = admin.post(f"/api/v1/stacks/iot/services/{service}/restart")
    assert r.status_code == 404
    assert not any(args.startswith("restart") for args, _ in calls())


# ── Logs ──────────────────────────────────────────────────────────────────────


def test_logs(admin, multi_project, calls):
    r = admin.get("/api/v1/stacks/iot/logs", params={"tail": 2, "service": "web"})
    assert r.status_code == 200
    assert r.json() == {
        "stack": "iot",
        "service": "web",
        "lines": ["web  | line 0", "web  | line 1"],
    }
    assert calls()[-1] == ("logs --no-color --tail 2 web", "iot")


def test_logs_limits_and_errors(admin, multi_project, calls, monkeypatch):
    assert admin.get("/api/v1/stacks/iot/logs", params={"tail": 0}).status_code == 422
    assert admin.get("/api/v1/stacks/iot/logs", params={"tail": 5001}).status_code == 422
    assert admin.get("/api/v1/stacks/iot/logs", params={"service": "nope"}).status_code == 404
    monkeypatch.setenv("FAKE_FAIL", "iot")
    r = admin.get("/api/v1/stacks/iot/logs")
    assert (r.status_code, r.json()["error"]["code"]) == (502, "upstream_error")
    assert "failing in iot" in r.json()["error"]["message"]


def test_logs_follow_streams_events(admin, multi_project, calls):
    with admin.stream("GET", "/api/v1/stacks/iot/logs", params={"follow": True}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["X-Accel-Buffering"] == "no"
        body = "".join(r.iter_text())
    events = [e for e in body.split("\n\n") if e]
    assert events[:3] == [f"data: web  | line {i}" for i in range(3)]
    assert events[-1] == "event: end\ndata: 0"
    assert calls()[-1] == ("logs --no-color --tail 200 --follow", "iot")


def test_follow_kills_compose_when_client_leaves(calls, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FOLLOW", "forever")
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))

    async def read_one_then_leave() -> str:
        stream = control.follow_events(control._logs_cmd(None, 1, True), tmp_path)
        first = await anext(stream)
        await stream.aclose()  # what the server does when the client disconnects
        return first

    assert asyncio.run(read_one_then_leave()) == "data: web  | line 0\n\n"
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


# ── Audit log ─────────────────────────────────────────────────────────────────


def test_stack_actions_are_audited(admin, multi_project, calls):
    job = admin.post("/api/v1/stacks/iot/up", headers={"X-Request-ID": "audit-1"}).json()
    _wait(admin, job["id"])
    entries = admin.get("/api/v1/audit").json()["items"]
    assert [(e["action"], e["target"], e["outcome"]) for e in entries[:2]] == [
        ("stack.up", "iot", "succeeded (0)"),
        ("stack.up", "iot", f"queued as job {job['id']}"),
    ]
    # The job runs in the background, yet its entry carries the request that queued it.
    assert {e["request_id"] for e in entries[:2]} == {"audit-1"}
    assert {e["actor"] for e in entries[:2]} == {"root"}


def test_user_and_device_changes_are_audited(admin):
    admin.post("/api/v1/users", json={"username": "alice", "password": "long enough pw"})
    admin.patch("/api/v1/users/alice", json={"role": "admin", "password": "another long pw"})
    admin.delete("/api/v1/users/alice")
    admin.post("/api/v1/devices", json={"id": "dev-1"})
    admin.patch("/api/v1/devices/dev-1", json={"enabled": False, "name": "x"})
    admin.post("/api/v1/devices/dev-1/key")
    admin.delete("/api/v1/devices/dev-1")
    assert _audit(admin) == [
        ("user.create", "alice", "role operator"),
        ("user.update", "alice", "role admin, password reset"),
        ("user.delete", "alice", ""),
        ("device.create", "dev-1", ""),
        ("device.update", "dev-1", "enabled, name"),
        ("device.rotate_key", "dev-1", ""),
        ("device.delete", "dev-1", ""),
    ]


def test_refused_changes_are_not_audited(admin):
    assert admin.delete("/api/v1/users/root").status_code == 409  # last admin
    assert _audit(admin) == []


def test_audit_is_admin_only_and_paginated(admin, client):
    for i in range(3):
        admin.post("/api/v1/devices", json={"id": f"dev-{i}"})
    assert client.get("/api/v1/audit").status_code == 403
    page = admin.get("/api/v1/audit", params={"limit": 2, "offset": 1}).json()
    assert [e["target"] for e in page["items"]] == ["dev-1", "dev-0"]
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
