"""Shared fixtures: a TestClient and synthetic p4n4 projects."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from p4n4_lib import env as envutil
from p4n4_lib import manifest as mf
from p4n4_lib.layers import LAYERS

from p4n4_api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


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
