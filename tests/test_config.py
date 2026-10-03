"""Tests for settings parsing from environment variables."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from p4n4_api.config import load_settings


def test_defaults(monkeypatch):
    for name in ("P4N4_PROJECT_DIR", "P4N4_API_DATA_DIR", "P4N4_API_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg")
    s = load_settings()
    assert (s.host, s.port, s.project_dir) == ("127.0.0.1", 8000, None)
    assert s.data_dir == Path("/xdg/p4n4-api")
    assert (s.cors_origins, s.trusted_proxies, s.auth_enabled, s.jwt_secret) == ((), (), True, None)


def test_comma_lists_and_paths(monkeypatch):
    monkeypatch.setenv("P4N4_API_CORS_ORIGINS", " http://a:8080/ ,, https://b ")
    monkeypatch.setenv("P4N4_API_TRUSTED_PROXIES", "172.16.0.0/12,10.0.0.1")
    monkeypatch.setenv("P4N4_PROJECT_DIR", "~/proj")
    s = load_settings()
    assert s.cors_origins == ("http://a:8080", "https://b")
    assert s.trusted_proxies == ("172.16.0.0/12", "10.0.0.1")
    assert s.project_dir == Path.home() / "proj"


@pytest.mark.parametrize(
    "value,enabled",
    [("off", False), ("false", False), ("0", False), ("no", False), ("on", True), ("TRUE", True)],
)
def test_auth_switch(monkeypatch, value, enabled):
    monkeypatch.setenv("P4N4_API_AUTH", value)
    assert load_settings().auth_enabled is enabled


def test_empty_means_unset(monkeypatch):
    monkeypatch.setenv("P4N4_API_JWT_SECRET", "")
    monkeypatch.setenv("P4N4_API_PORT", "")
    s = load_settings()
    assert (s.jwt_secret, s.port) == (None, 8000)


@pytest.mark.parametrize(
    "name,value",
    [("P4N4_API_PORT", "http"), ("P4N4_API_PORT", "70000"), ("P4N4_API_AUTH", "maybe")],
)
def test_bad_values_fail(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=f"(?i){name.removeprefix('P4N4_API_')}"):
        load_settings()


def test_unrelated_variables_ignored(monkeypatch):
    # The dashboard's container sets P4N4_API_UPSTREAM; it isn't ours.
    monkeypatch.setenv("P4N4_API_UPSTREAM", "http://host.docker.internal:8000")
    load_settings()
