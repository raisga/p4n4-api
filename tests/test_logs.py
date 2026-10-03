"""Tests for request IDs, the access log and log formatting."""

from __future__ import annotations

import json
import logging
import logging.config

import pytest

from p4n4_api import logs
from p4n4_api.cli import main as cli


def test_request_id_generated(anon):
    r = anon.get("/health")
    rid = r.headers["X-Request-ID"]
    assert len(rid) == 32
    assert anon.get("/health").headers["X-Request-ID"] != rid


def test_request_id_passed_through(anon):
    r = anon.get("/health", headers={"X-Request-ID": "dash-1234.abc"})
    assert r.headers["X-Request-ID"] == "dash-1234.abc"


@pytest.mark.parametrize("bad", ["x" * 65, "has space", "line\\nbreak", ""])
def test_odd_request_ids_replaced(anon, bad):
    r = anon.get("/health", headers={"X-Request-ID": bad})
    assert r.headers["X-Request-ID"] != bad
    assert len(r.headers["X-Request-ID"]) == 32


def test_request_id_on_errors(anon):
    r = anon.get("/api/v1/project", headers={"X-Request-ID": "err-1"})
    assert r.status_code == 401
    assert r.headers["X-Request-ID"] == "err-1"


def test_access_log(anon, caplog):
    caplog.set_level(logging.INFO, logger="p4n4_api.access")
    anon.get("/api/v1/project", headers={"X-Request-ID": "req-7"})
    (record,) = [r for r in caplog.records if r.name == "p4n4_api.access"]
    assert (record.method, record.path, record.status) == ("GET", "/api/v1/project", 401)
    assert record.client == "testclient"
    assert record.duration_ms >= 0
    assert record.getMessage().startswith("GET /api/v1/project 401 ")


def test_request_id_on_log_lines_inside_a_request(anon, caplog, monkeypatch):
    # Anything logged while handling a request carries its ID.
    seen = []
    caplog.handler.addFilter(logs.RequestIdFilter())

    def spy(*args, **kwargs):
        logging.getLogger("p4n4_api.test").warning("inside")
        seen.append(logs.request_id.get())
        return "1.0"

    monkeypatch.setattr("p4n4_api.routes.health._lib_version", spy)
    anon.get("/api/v1/version", headers={"X-Request-ID": "inner-1"})
    assert seen == ["inner-1"]
    inner = [r for r in caplog.records if r.name == "p4n4_api.test"]
    assert inner[0].request_id == "inner-1"
    assert logs.request_id.get() == "-"  # reset afterwards


def test_json_formatter():
    record = logging.LogRecord("p4n4_api.access", logging.INFO, "", 0, "GET / 200", (), None)
    record.request_id = "abc"
    record.method, record.status = "GET", 200
    entry = json.loads(logs.JsonFormatter().format(record))
    assert entry["level"] == "info"
    assert (entry["request_id"], entry["method"], entry["status"]) == ("abc", "GET", 200)
    assert entry["message"] == "GET / 200"
    assert entry["ts"].endswith("+00:00")


@pytest.fixture()
def restore_logging():
    """Put the loggers log_config touches back as they were."""
    loggers = [logging.getLogger(n) for n in ("p4n4_api", "uvicorn")]
    saved = {lg.name: (lg.handlers[:], lg.level, lg.propagate) for lg in loggers}
    yield
    for name, (handlers, level, propagate) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_log_config_applies(fmt, capsys, restore_logging):
    logging.config.dictConfig(logs.log_config(fmt, "info"))
    token = logs.request_id.set("cfg-1")
    try:
        logging.getLogger("p4n4_api.something").info("hello")
        logging.getLogger("p4n4_api.something").debug("hidden at info")
    finally:
        logs.request_id.reset(token)
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1
    if fmt == "json":
        entry = json.loads(lines[0])
        assert (entry["message"], entry["request_id"]) == ("hello", "cfg-1")
    else:
        assert lines[0].endswith("INFO    p4n4_api.something [cfg-1] hello")


def test_serve_uses_our_logging(monkeypatch):
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setenv("P4N4_API_LOG_FORMAT", "JSON")
    assert cli(["serve"]) == 0
    assert calls[0]["access_log"] is False
    assert calls[0]["log_config"]["formatters"]["default"] == {"()": logs.JsonFormatter}


def test_cors_exposes_request_id(monkeypatch):
    from fastapi.testclient import TestClient

    from p4n4_api.main import create_app

    monkeypatch.setenv("P4N4_API_CORS_ORIGINS", "http://localhost:5173")
    c = TestClient(create_app())
    r = c.get("/health", headers={"Origin": "http://localhost:5173"})
    assert "x-request-id" in r.headers["Access-Control-Expose-Headers"].lower()
