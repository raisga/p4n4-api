"""Shared fixtures: a TestClient and synthetic p4n4 projects."""

from __future__ import annotations

import socket
import subprocess
import time

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from p4n4_lib import env as envutil
from p4n4_lib import manifest as mf
from p4n4_lib.layers import LAYERS

from p4n4_api import ai, auth, db, edge_runner, influx, users
from p4n4_api.main import app

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def api_state(tmp_path_factory, monkeypatch):
    """A fresh data dir (database, JWT secret) per test, auth on, and fast password hashing."""
    data_dir = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("P4N4_API_DATA_DIR", str(data_dir))
    monkeypatch.delenv("P4N4_API_AUTH", raising=False)
    monkeypatch.delenv("P4N4_API_JWT_SECRET", raising=False)
    # No broker in most tests; MQTT tests start their own (tests/test_telemetry.py).
    monkeypatch.setenv("P4N4_API_MQTT_ENABLED", "false")
    monkeypatch.setattr(users, "hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    users._dummy_hash.cache_clear()
    auth.login_limiter.reset()
    return data_dir


def make_user(username: str, role: str) -> None:
    with db.connect() as conn:
        users.create(conn, username, PASSWORD, role)


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict:
    r = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def anon() -> TestClient:
    """A client with no credentials."""
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    """A client signed in as an operator, the role every read endpoint needs."""
    c = TestClient(app)
    make_user("ops", "operator")
    c.headers["Authorization"] = f"Bearer {login(c, 'ops')['access_token']}"
    return c


@pytest.fixture()
def admin() -> TestClient:
    c = TestClient(app)
    make_user("root", "admin")
    c.headers["Authorization"] = f"Bearer {login(c, 'root')['access_token']}"
    return c


def _populate_layer(base, name):
    layer = LAYERS[name]
    for rel in layer.required_files:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    envutil.write(base / ".env", {key: "x" for key in layer.required_env_keys})


@pytest.fixture()
def flat_project(tmp_path, monkeypatch):
    """A flat single-layer (iot) project, exported via P4N4_PROJECT_DIR."""
    mf.save(tmp_path / mf.MANIFEST_FILE, mf.create("proj", ["iot"]))
    _populate_layer(tmp_path, "iot")
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def multi_project(tmp_path, monkeypatch):
    """A multi-layer (iot+ai) project, exported via P4N4_PROJECT_DIR."""
    mf.save(tmp_path / mf.MANIFEST_FILE, mf.create("proj-multi", ["iot", "ai"]))
    for name in ("iot", "ai"):
        (tmp_path / name).mkdir()
        _populate_layer(tmp_path / name, name)
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    return tmp_path


def _no_influxdb(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no InfluxDB in tests", request=request)


@pytest.fixture(autouse=True)
def influxdb(monkeypatch):
    """InfluxDB calls never leave the test: they fail as unreachable unless a test sets
    `influxdb.handler` to answer them (an httpx request → response function)."""

    class Fake:
        handler = staticmethod(_no_influxdb)
        requests: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        Fake.requests.append(request)
        return Fake.handler(request)

    Fake.requests = []
    monkeypatch.setattr(influx, "transport", httpx.MockTransport(route))
    return Fake


def _no_runner(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no edge runner in tests", request=request)


@pytest.fixture(autouse=True)
def runner(monkeypatch):
    """Edge runner calls never leave the test: unreachable unless a test sets
    `runner.handler`."""

    class Fake:
        handler = staticmethod(_no_runner)
        requests: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        Fake.requests.append(request)
        return Fake.handler(request)

    Fake.requests = []
    monkeypatch.setattr(edge_runner, "transport", httpx.MockTransport(route))
    edge_runner.clear_cache()
    return Fake


def _no_ai_stack(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no Ollama or Letta in tests", request=request)


@pytest.fixture(autouse=True)
def ai_stack(monkeypatch):
    """Ollama and Letta calls never leave the test: unreachable unless a test sets
    `ai_stack.handler`."""

    class Fake:
        handler = staticmethod(_no_ai_stack)
        requests: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        Fake.requests.append(request)
        return Fake.handler(request)

    Fake.requests = []
    monkeypatch.setattr(ai, "transport", httpx.MockTransport(route))
    return Fake


@pytest.fixture()
def edge_project(tmp_path, monkeypatch):
    """A multi-layer (iot+edge) project, exported via P4N4_PROJECT_DIR."""
    mf.save(tmp_path / mf.MANIFEST_FILE, mf.create("proj-edge", ["iot", "edge"]))
    for name in ("iot", "edge"):
        (tmp_path / name).mkdir()
        _populate_layer(tmp_path / name, name)
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def docker_up(monkeypatch):
    """Pretend the Docker daemon is reachable; tests override these to simulate failures."""
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: None)
    monkeypatch.setattr("p4n4_api.docker.started_at", lambda ids: {})


@pytest.fixture()
def broker():
    """A throwaway mosquitto on a free local port (tests using it skip without mosquitto)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        ["mosquitto", "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    deadline = time.monotonic() + 5
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            assert time.monotonic() < deadline, "mosquitto didn't start"
            time.sleep(0.05)
    yield port
    proc.terminate()
    proc.wait(5)
