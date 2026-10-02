"""Shared fixtures: a TestClient and synthetic p4n4 projects."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from p4n4_lib import env as envutil
from p4n4_lib import manifest as mf
from p4n4_lib.layers import LAYERS

from p4n4_api import auth, db, users
from p4n4_api.main import app

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def api_state(tmp_path_factory, monkeypatch):
    """A fresh data dir (database, JWT secret) per test, auth on, and fast password hashing."""
    data_dir = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("P4N4_API_DATA_DIR", str(data_dir))
    monkeypatch.delenv("P4N4_API_AUTH", raising=False)
    monkeypatch.delenv("P4N4_API_JWT_SECRET", raising=False)
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


@pytest.fixture(autouse=True)
def docker_up(monkeypatch):
    """Pretend the Docker daemon is reachable; tests override these to simulate failures."""
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: None)
    monkeypatch.setattr("p4n4_api.docker.started_at", lambda ids: {})
