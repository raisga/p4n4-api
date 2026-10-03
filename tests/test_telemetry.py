"""Tests for telemetry: line protocol and Flux, ingest and query against a fake InfluxDB,
and MQTT publishing and live streams against a real (throwaway) mosquitto."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs

import aiomqtt
import httpx
import pytest
from fastapi.testclient import TestClient

from p4n4_api import influx
from p4n4_api.mqtt import STORED_MARKER, Bridge, reading_from
from p4n4_api.routes import telemetry

# ── Line protocol, Flux, CSV ──────────────────────────────────────────────────


def test_line_matches_node_red_schema():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    line = influx.line("greenhouse-01", "temp", {"value": 23, "unit": "C", "ok": True}, at)
    # Ints are written as floats ("23.0", no "i"), as Node-RED does: one type per field.
    assert (
        line
        == 'sensor_data,device=greenhouse-01,sensor=temp value=23.0,unit="C",ok=true 1767225600000'
    )


def test_line_escaping():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    line = influx.line("a b", "x,y", {"k=1": 'say "hi"\nnow', "path": "c:\\d"}, at)
    assert line.startswith(r"sensor_data,device=a\ b,sensor=x\,y k\=1=")
    assert r'"say \"hi\" now"' in line
    assert r'path="c:\\d"' in line
    assert influx.line("d", "s", {"v": float("inf")}, at) is None


def test_flux_quotes_every_value():
    flux = influx.build_query(
        "raw_telemetry",
        start="-1h",
        stop="2026-01-01T00:00:00+02:00",
        device='x") |> drop() //',
        sensor="${secret}",
        field=None,
        every="5m",
        agg="max",
        limit=10,
    )
    assert 'r.device == "x\\") |> drop() //")' in flux
    assert 'r.sensor == "\\${secret}")' in flux
    assert "range(start: -1h, stop: 2025-12-31T22:00:00.000000Z)" in flux
    assert "aggregateWindow(every: 5m, fn: max, createEmpty: false)" in flux
    assert flux.rstrip().endswith("|> limit(n: 10)")


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"start": "yesterday"}, "neither a duration"),
        ({"start": "-1h); drop("}, "neither a duration"),
        ({"every": "-5m"}, "not a duration"),
        ({"every": "5 minutes"}, "not a duration"),
        ({"agg": "exec"}, "agg must be"),
    ],
)
def test_flux_rejects_bad_values(kwargs, message):
    args = {"start": "-1h", "stop": None, "device": None, "sensor": None, "field": None}
    args |= {"every": None, "agg": "mean", "limit": 10} | kwargs
    with pytest.raises(ValueError, match=message):
        influx.build_query("b", **args)


CSV = (
    "#datatype,string,long,dateTime:RFC3339,double,string,string,string\r\n"
    ",result,table,_time,_value,_field,device,sensor\r\n"
    ",_result,0,2026-01-01T00:00:00Z,21.5,value,g1,temp\r\n"
    ",_result,0,2026-01-01T00:01:00Z,22,value,g1,temp\r\n"
    "\r\n"
    "#datatype,string,long,dateTime:RFC3339,string,string,string,string\r\n"
    ",result,table,_time,_value,_field,device,sensor\r\n"
    ",_result,1,2026-01-01T00:00:00Z,C,unit,g1,temp\r\n"
    "\r\n"
)


def test_parse_csv_types_each_table():
    rows = influx.parse_csv(CSV)
    assert [r["_value"] for r in rows] == [21.5, 22.0, "C"]
    assert rows[0] == {
        "_time": "2026-01-01T00:00:00Z",
        "_value": 21.5,
        "_field": "value",
        "device": "g1",
        "sensor": "temp",
    }


# ── Ingest ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def device(admin, anon):
    """A client signed in as device greenhouse-01."""
    key = admin.post("/api/v1/devices", json={"id": "greenhouse-01"}).json()["api_key"]
    token = anon.post("/api/v1/auth/token", json={"api_key": key}).json()["access_token"]
    c = TestClient(anon.app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.fixture()
def stored(influxdb):
    """InfluxDB accepts writes; returns the line-protocol bodies it got."""
    bodies: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(204)

    influxdb.handler = handle
    return bodies


def _send(c, readings):
    return c.post("/api/v1/telemetry", json={"readings": readings})


def test_ingest_stores_readings(device, flat_project, stored, influxdb):
    r = _send(
        device,
        [
            {
                "sensor": "temp",
                "fields": {"value": 21.5, "unit": "C"},
                "ts": "2026-01-01T00:00:00Z",
            },
            {"sensor": "humidity", "fields": {"value": 40}, "ts": 1767225660},
        ],
    )
    assert r.status_code == 201
    assert r.json() == {"stored": 2, "published": 0}  # no broker in this test
    assert stored == [
        'sensor_data,device=greenhouse-01,sensor=temp value=21.5,unit="C" 1767225600000\n'
        "sensor_data,device=greenhouse-01,sensor=humidity value=40.0 1767225660000"
    ]
    request = influxdb.requests[-1]
    # Org, bucket and token come from the project's iot/.env (all "x" in the fixture).
    assert parse_qs(request.url.query.decode()) == {
        "org": ["x"],
        "bucket": ["x"],
        "precision": ["ms"],
    }
    assert request.headers["Authorization"] == "Token x"


def test_ingest_timestamps(device, flat_project, stored):
    before = int(time.time() * 1000)
    _send(
        device,
        [
            {"sensor": "t", "fields": {"v": 1}},
            {"sensor": "t", "fields": {"v": 2}, "ts": "2026-01-01T00:00:00"},
        ],
    )
    first, second = stored[0].splitlines()
    assert before <= int(first.rsplit(" ", 1)[1]) <= int(time.time() * 1000)
    assert second.endswith(" 1767225600000")  # naive times are UTC


def test_settings_override_stack_env(device, flat_project, stored, influxdb, monkeypatch):
    monkeypatch.setenv("P4N4_API_INFLUXDB_URL", "http://influx.example:9999/")
    monkeypatch.setenv("P4N4_API_INFLUXDB_TOKEN", "from-env")
    monkeypatch.setenv("P4N4_API_INFLUXDB_BUCKET", "other")
    _send(device, [{"sensor": "t", "fields": {"v": 1}}])
    request = influxdb.requests[-1]
    assert str(request.url).startswith("http://influx.example:9999/api/v2/write?")
    assert request.headers["Authorization"] == "Token from-env"
    assert parse_qs(request.url.query.decode())["bucket"] == ["other"]
    assert parse_qs(request.url.query.decode())["org"] == ["x"]  # still from iot/.env


@pytest.mark.parametrize(
    "reading",
    [
        {"sensor": "a/b", "fields": {"v": 1}},
        {"sensor": "+", "fields": {"v": 1}},
        {"sensor": "#", "fields": {"v": 1}},
        {"sensor": "", "fields": {"v": 1}},
        {"sensor": "t", "fields": {}},
        {"sensor": "t", "fields": {"device": "other"}},
        {"sensor": "t", "fields": {"ts": 1}},
        {"sensor": "t", "fields": {"_stored_by": "p4n4-api"}},
        {"sensor": "t", "fields": {"v": [1, 2]}},
        {"sensor": "t", "fields": {"v": "x" * 1025}},
    ],
)
def test_ingest_rejects_bad_readings(device, flat_project, stored, reading):
    r = _send(device, [reading])
    assert r.status_code == 422, r.text
    assert stored == []


def test_ingest_rejects_nan(device, flat_project, stored):
    # JSON has no NaN, but Python's parser accepts it; it can't be stored.
    body = '{"readings": [{"sensor": "t", "fields": {"v": NaN}}]}'
    r = device.post("/api/v1/telemetry", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "finite" in r.json()["error"]["message"]
    assert stored == []


def test_ingest_batch_limits(device, flat_project, stored):
    assert _send(device, []).status_code == 422
    assert _send(device, [{"sensor": "t", "fields": {"v": 1}}] * 1001).status_code == 422


def test_only_devices_ingest(client, admin, anon, flat_project, stored):
    reading = [{"sensor": "t", "fields": {"v": 1}}]
    assert _send(client, reading).status_code == 403
    assert _send(admin, reading).status_code == 403
    assert _send(anon, reading).status_code == 401


@pytest.mark.parametrize(
    "response,status,code",
    [
        (None, 503, "influxdb_unavailable"),
        (httpx.Response(400, json={"message": "field type conflict"}), 422, "influxdb_rejected"),
        (httpx.Response(500, text="boom"), 502, "upstream_error"),
    ],
)
def test_ingest_influx_errors(device, flat_project, influxdb, response, status, code):
    if response is not None:
        influxdb.handler = lambda request: response
    r = _send(device, [{"sensor": "t", "fields": {"v": 1}}])
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    if code == "influxdb_rejected":
        assert r.json()["error"]["message"] == "InfluxDB: field type conflict"


def test_ingest_without_influx_config(device, tmp_path, monkeypatch, stored):
    from p4n4_lib import manifest as mf

    mf.save(tmp_path / mf.MANIFEST_FILE, mf.create("ai-only", ["ai"]))
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    r = _send(device, [{"sensor": "t", "fields": {"v": 1}}])
    assert (r.status_code, r.json()["error"]["code"]) == (503, "influxdb_not_configured")


def test_ingest_updates_last_seen_throttled(admin, device, flat_project, stored):
    from p4n4_api import db

    with db.connect() as conn:
        conn.execute("UPDATE devices SET last_seen_at = '2020-01-01T00:00:00+00:00'")
    _send(device, [{"sensor": "t", "fields": {"v": 1}}])
    seen = admin.get("/api/v1/devices/greenhouse-01").json()["last_seen_at"]
    assert seen > "2026"
    with db.connect() as conn:
        conn.execute("UPDATE devices SET last_seen_at = ?", (seen[:-6] + ".5" + seen[-6:],))
        marker = conn.execute("SELECT last_seen_at FROM devices").fetchone()[0]
    _send(device, [{"sensor": "t", "fields": {"v": 1}}])
    with db.connect() as conn:  # within a minute: not written again
        assert conn.execute("SELECT last_seen_at FROM devices").fetchone()[0] == marker


# ── Query ─────────────────────────────────────────────────────────────────────


def test_query(client, flat_project, influxdb):
    influxdb.handler = lambda request: httpx.Response(200, text=CSV)
    r = client.get(
        "/api/v1/telemetry",
        params={
            "device": "g1",
            "sensor": "temp",
            "start": "-6h",
            "every": "1m",
            "agg": "max",
            "limit": 3,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert body["points"][1] == {
        "time": "2026-01-01T00:01:00Z",
        "device": "g1",
        "sensor": "temp",
        "field": "value",
        "value": 22.0,
    }
    sent = json.loads(influxdb.requests[-1].content)
    assert 'from(bucket: "x")' in sent["query"]
    assert 'r.device == "g1"' in sent["query"]
    assert "range(start: -6h)" in sent["query"]
    assert sent["dialect"] == {"header": True, "annotations": ["datatype"]}


def test_query_validation(client, flat_project, influxdb):
    assert client.get("/api/v1/telemetry", params={"start": "last week"}).status_code == 422
    assert client.get("/api/v1/telemetry", params={"every": "1 minute"}).status_code == 422
    assert client.get("/api/v1/telemetry", params={"agg": "exec"}).status_code == 422
    assert client.get("/api/v1/telemetry", params={"limit": 10_001}).status_code == 422
    assert influxdb.requests == []


def test_query_needs_operator(device, anon, flat_project):
    assert device.get("/api/v1/telemetry").status_code == 403
    assert anon.get("/api/v1/telemetry").status_code == 401
    assert device.get("/api/v1/telemetry/stream").status_code == 403


# ── MQTT (real broker) ────────────────────────────────────────────────────────

needs_mosquitto = pytest.mark.skipif(
    not shutil.which("mosquitto"), reason="mosquitto not installed"
)


def test_reading_from_topic_and_payload():
    r = reading_from("sensors/g1/temp", b'{"value": 21.5, "device": "spoof", "model": "m"}')
    assert (r["device"], r["sensor"], r["fields"]) == ("g1", "temp", {"value": 21.5})
    # Only the API's own marked readings carry a trusted ts.
    marked = json.dumps(
        {"value": 1, "ts": "2026-01-01T00:00:00.000+00:00", STORED_MARKER: "p4n4-api"}
    )
    assert reading_from("sensors/g1/temp", marked.encode())["ts"] == "2026-01-01T00:00:00.000+00:00"
    assert reading_from("sensors/g1/temp", b'{"value": 1, "ts": "2000"}')["ts"] != "2000"
    for topic, payload in [
        ("sensors/g1", b"{}"),
        ("other/g1/t", b"{}"),
        ("sensors/g1/t", b"[1]"),
        ("sensors/g1/t", b"nope"),
    ]:
        assert reading_from(topic, payload) is None


@needs_mosquitto
def test_ingest_publishes_marked_readings(device, flat_project, stored, broker, monkeypatch):
    monkeypatch.setenv("P4N4_API_MQTT_ENABLED", "true")
    monkeypatch.setenv("P4N4_API_MQTT_HOST", "127.0.0.1")
    monkeypatch.setenv("P4N4_API_MQTT_PORT", str(broker))
    from p4n4_api.main import app
    from p4n4_api.mqtt import bridge

    watcher = subprocess.Popen(
        ["mosquitto_sub", "-p", str(broker), "-t", "sensors/#", "-C", "1", "-v", "-W", "10"],
        stdout=subprocess.PIPE,
        text=True,
    )
    with TestClient(app) as c:
        c.headers.update(device.headers)
        deadline = time.monotonic() + 5
        while not bridge.connected:
            assert time.monotonic() < deadline, "bridge didn't connect"
            time.sleep(0.05)
        time.sleep(0.2)  # let mosquitto_sub subscribe
        r = c.post(
            "/api/v1/telemetry",
            json={
                "readings": [
                    {"sensor": "temp", "fields": {"value": 21.5}, "ts": "2026-01-01T00:00:00Z"}
                ]
            },
        )
        assert r.json() == {"stored": 1, "published": 1}
    topic, payload = watcher.communicate(timeout=10)[0].strip().split(" ", 1)
    assert topic == "sensors/greenhouse-01/temp"
    assert json.loads(payload) == {
        "value": 21.5,
        "ts": "2026-01-01T00:00:00.000+00:00",
        STORED_MARKER: "p4n4-api",
    }


@needs_mosquitto
def test_live_stream(broker):
    async def scenario() -> list[str]:
        bridge = Bridge()
        bridge.start("127.0.0.1", broker)
        try:
            for _ in range(100):
                if bridge.connected:
                    break
                await asyncio.sleep(0.05)
            original = telemetry.bridge
            telemetry.bridge = bridge
            try:
                events = telemetry.stream_events("g1", None)
                status = await anext(events)
                async with aiomqtt.Client("127.0.0.1", broker) as device:
                    await device.publish("sensors/g2/temp", json.dumps({"value": 1}))  # filtered
                    await device.publish("sensors/g1/temp", json.dumps({"value": 2}))
                    reading = await asyncio.wait_for(anext(events), 5)
                await events.aclose()
                assert bridge.subscriber_count == 0  # unsubscribed when the client left
                return [status, reading]
            finally:
                telemetry.bridge = original
        finally:
            await bridge.stop()

    status, reading = asyncio.run(scenario())
    assert status == 'event: status\ndata: {"mqtt": true}\n\n'
    assert reading.startswith("event: reading\ndata: ")
    data = json.loads(reading.split("data: ", 1)[1])
    assert (data["device"], data["sensor"], data["fields"]) == ("g1", "temp", {"value": 2})


def test_publish_without_broker_still_reaches_streams():
    async def scenario() -> dict:
        bridge = Bridge()  # never started: no broker
        async with bridge.subscribe() as subscriber:
            assert await bridge.publish("g1", "temp", {"value": 1}, "2026-01-01T00:00:00Z") is False
            return subscriber.queue.get_nowait()

    reading = asyncio.run(scenario())
    assert (reading["device"], reading["fields"], reading["ts"]) == (
        "g1",
        {"value": 1},
        "2026-01-01T00:00:00Z",
    )


def test_slow_subscriber_drops_oldest(monkeypatch):
    from p4n4_api import mqtt

    monkeypatch.setattr(mqtt, "QUEUE_SIZE", 2)

    async def scenario():
        bridge = Bridge()
        async with bridge.subscribe() as subscriber:
            for i in range(3):
                bridge.dispatch({"device": "d", "sensor": "s", "fields": {"i": i}})
            return subscriber.dropped, [
                subscriber.queue.get_nowait()["fields"]["i"] for _ in range(2)
            ]

    assert asyncio.run(scenario()) == (1, [1, 2])
