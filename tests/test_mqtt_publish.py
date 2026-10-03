"""Tests for POST /api/v1/mqtt/publish and MQTT topic rules."""

from __future__ import annotations

import json
import shutil
import subprocess
import time

import aiomqtt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from p4n4_api import topics
from p4n4_api.config import load_settings
from p4n4_api.mqtt import bridge

# ── Topic rules ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "topic_filter,topic,expected",
    [
        ("#", "a/b", True),
        ("sensors/#", "sensors/g1/temp", True),
        ("sensors/#", "sensors", True),  # '#' includes the parent level
        ("sensors/+/temp", "sensors/g1/temp", True),
        ("sensors/+/temp", "sensors/g1/hum", False),
        ("sensors/+", "sensors/g1/temp", False),
        ("a/b", "a/b", True),
        ("a/b", "a/b/c", False),
        ("+/+", "a/", True),  # an empty level is a level
        ("#", "$SYS/broker", False),  # first-level wildcards skip $ topics
        ("$SYS/#", "$SYS/broker", True),
    ],
)
def test_matches(topic_filter, topic, expected):
    assert topics.matches(topic_filter, topic) is expected


@pytest.mark.parametrize("bad", ["", "a/#/b", "a#", "a/b+", "x" * 257])
def test_check_filter_rejects(bad):
    with pytest.raises(ValueError):
        topics.check_filter(bad)


@pytest.mark.parametrize("bad", ["", "a/+/b", "a/#", "$SYS/x", "a\0b", "x" * 257])
def test_check_topic_rejects(bad):
    with pytest.raises(ValueError):
        topics.check_topic(bad)


def test_publish_settings(monkeypatch):
    s = load_settings()
    assert (s.mqtt_publish_allow, s.mqtt_publish_deny) == (("#",), ("sensors/#", "inference/#"))
    monkeypatch.setenv("P4N4_API_MQTT_PUBLISH_ALLOW", "commands/+/set, sandbox/#")
    monkeypatch.setenv("P4N4_API_MQTT_PUBLISH_DENY", "none")
    s = load_settings()
    assert (s.mqtt_publish_allow, s.mqtt_publish_deny) == (("commands/+/set", "sandbox/#"), ())
    monkeypatch.setenv("P4N4_API_MQTT_PUBLISH_DENY", "a/#/b")
    with pytest.raises(ValidationError, match="must be a whole, last level"):
        load_settings()


# ── Endpoint (fake connection) ────────────────────────────────────────────────


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple] = []
        self.error = error

    async def publish(self, topic, payload, qos=0, retain=False, timeout=None):
        if self.error:
            raise self.error
        self.sent.append((topic, payload, qos, retain))


@pytest.fixture()
def connected(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(bridge, "_client", fake)
    return fake


def _publish(c, **body):
    return c.post("/api/v1/mqtt/publish", json=body)


def test_publish_string_and_json(client, admin, connected):
    r = _publish(client, topic="commands/pump-1/set", payload="ON", qos=1)
    assert r.status_code == 200
    assert r.json() == {"topic": "commands/pump-1/set", "qos": 1, "retain": False, "bytes": 2}
    _publish(client, topic="commands/pump-1/set", payload={"speed": 3}, retain=True)
    _publish(client, topic="x", payload=42)
    assert connected.sent == [
        ("commands/pump-1/set", b"ON", 1, False),
        ("commands/pump-1/set", b'{"speed": 3}', 0, True),
        ("x", b"42", 0, False),
    ]
    entries = admin.get("/api/v1/audit").json()["items"]
    assert [(e["actor"], e["action"], e["target"], e["outcome"]) for e in entries[:2]] == [
        ("ops", "mqtt.publish", "x", "qos 0, 2 bytes"),
        ("ops", "mqtt.publish", "commands/pump-1/set", "qos 0, 12 bytes, retained"),
    ]


def test_clear_retained_with_empty_payload(client, connected):
    assert _publish(client, topic="status/last", retain=True).json()["bytes"] == 0
    assert connected.sent == [("status/last", b"", 0, True)]


@pytest.mark.parametrize("topic", ["sensors/g1/temp", "sensors", "inference/g1/result"])
def test_device_data_topics_denied_by_default(client, connected, topic):
    r = _publish(client, topic=topic, payload={"value": 99})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "topic_not_allowed")
    assert connected.sent == []


def test_sandbox_topics_allowed(client, connected):
    # The sandbox flow is how people test the pipeline with made-up readings.
    assert (
        _publish(client, topic="sandbox/sensors/g1/temp", payload={"value": 1}).status_code == 200
    )


def test_allow_and_deny_lists(client, connected, monkeypatch):
    monkeypatch.setenv("P4N4_API_MQTT_PUBLISH_ALLOW", "commands/#")
    assert _publish(client, topic="commands/a").status_code == 200
    assert _publish(client, topic="other/a").status_code == 403
    monkeypatch.setenv("P4N4_API_MQTT_PUBLISH_ALLOW", "#")
    monkeypatch.setenv("P4N4_API_MQTT_PUBLISH_DENY", "none")
    assert _publish(client, topic="sensors/g1/temp").status_code == 200


@pytest.mark.parametrize("topic", ["", "a/+", "a/#", "$SYS/broker/uptime", "x" * 257])
def test_bad_topics(client, connected, topic):
    assert _publish(client, topic=topic).status_code == 422
    assert connected.sent == []


def test_bad_qos(client, connected):
    assert _publish(client, topic="a", qos=3).status_code == 422


def test_payload_limit(client, connected):
    r = _publish(client, topic="a", payload="x" * (256 * 1024 + 1))
    assert (r.status_code, r.json()["error"]["code"]) == (413, "payload_too_large")


def test_not_connected(client):
    r = _publish(client, topic="a")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "mqtt_unavailable")


def test_publish_failure(client, monkeypatch, admin):
    monkeypatch.setattr(bridge, "_client", FakeClient(aiomqtt.MqttError("timed out")))
    r = _publish(client, topic="a", qos=1)
    assert (r.status_code, r.json()["error"]["code"]) == (502, "upstream_error")
    assert [e["action"] for e in admin.get("/api/v1/audit").json()["items"]] == []


def test_publish_needs_operator(anon, admin, connected):
    assert _publish(anon, topic="a").status_code == 401
    key = admin.post("/api/v1/devices", json={"id": "dev-1"}).json()["api_key"]
    token = anon.post("/api/v1/auth/token", json={"api_key": key}).json()["access_token"]
    r = anon.post(
        "/api/v1/mqtt/publish", json={"topic": "a"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


# ── Real broker ───────────────────────────────────────────────────────────────


@pytest.mark.skipif(not shutil.which("mosquitto_sub"), reason="mosquitto not installed")
def test_retained_publish_reaches_broker(client, broker, monkeypatch):
    monkeypatch.setenv("P4N4_API_MQTT_ENABLED", "true")
    monkeypatch.setenv("P4N4_API_MQTT_HOST", "127.0.0.1")
    monkeypatch.setenv("P4N4_API_MQTT_PORT", str(broker))
    from p4n4_api.main import app

    with TestClient(app) as c:
        c.headers.update(client.headers)
        deadline = time.monotonic() + 5
        while not bridge.connected:
            assert time.monotonic() < deadline, "bridge didn't connect"
            time.sleep(0.05)
        r = c.post(
            "/api/v1/mqtt/publish",
            json={
                "topic": "commands/pump-1/set",
                "payload": {"on": True},
                "qos": 1,
                "retain": True,
            },
        )
        assert r.status_code == 200, r.text
    # Retained: a subscriber that connects afterwards still gets it.
    out = subprocess.run(
        ["mosquitto_sub", "-p", str(broker), "-t", "commands/#", "-C", "1", "-v", "-W", "5"],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    topic, payload = out.split(" ", 1)
    assert (topic, json.loads(payload)) == ("commands/pump-1/set", {"on": True})
