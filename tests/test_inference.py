"""Tests for inference through the edge runner (Edge Impulse, ONNX and mock backends)."""

from __future__ import annotations

import json

import httpx
import pytest

from p4n4_api import edge_runner

# /api/v1/info as each backend of stacks/edge runner/runner.py reports it
EIM_INFO = {
    "backend": "model",
    "model_file": "/models/model.eim",
    "model": {
        "project": "vibration",
        "input_features_count": 3,
        "labels": ["idle", "running"],
        "has_anomaly": True,
    },
    "labels": [],
}
ONNX_INFO = {
    "backend": "onnx",
    "model_file": "/onnx-models/model.onnx",
    "model": {"input_name": "input", "input_shape": ["batch", 4]},
    "labels": ["idle", "running", "anomaly"],
}
MOCK_INFO = {"backend": "mock", "model_file": None, "model": {}, "labels": []}
HEALTH = {
    "status": "ok",
    "mode": "onnx",
    "inference_count": 12,
    "last_inference_at": "2026-10-02T12:00:00+00:00",
    "last_latency_ms": 3.25,
    "mqtt_connected": True,
    "influxdb_ok": True,
}


def _result(mode: str, device: str = "api") -> dict:
    return {
        "device": device,
        "timestamp": "2026-10-02T12:00:00+00:00",
        "label": "running",
        "confidence": 0.91,
        "anomaly_score": 0.0,
        "latency_ms": 2.5,
        "mode": mode,
    }


def fake_runner(info: dict, infer=None, health=HEALTH):
    """A runner answering /api/v1/info with `info`, /health with `health`, and
    /api/v1/infer with `infer` (a response, or by default a result in the info's mode)."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/info":
            return httpx.Response(200, json=info)
        if request.url.path == "/health":
            return httpx.Response(200, json=health)
        if request.url.path == "/api/v1/infer":
            if infer is not None:
                return infer
            body = json.loads(request.content)
            return httpx.Response(200, json=_result(info["backend"], body["device"]))
        return httpx.Response(404)

    return handle


def _paths(runner) -> list[str]:
    return [r.url.path for r in runner.requests]


# ── Model description ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "info,expected",
    [
        (EIM_INFO, 3),
        (ONNX_INFO, 4),
        ({"backend": "onnx", "model": {"input_shape": [1, 2, 3]}}, 6),
        ({"backend": "onnx", "model": {"input_shape": [None, "seq", 4]}}, None),
        ({"backend": "onnx", "model": {"input_shape": []}}, None),
        ({"backend": "model", "model": {"input_features_count": 0}}, None),
        (MOCK_INFO, None),
    ],
)
def test_expected_features(info, expected):
    assert edge_runner.expected_features(info) == expected


@pytest.mark.parametrize(
    "info,backend,labels,expected",
    [
        (EIM_INFO, "edge-impulse", ["idle", "running"], 3),
        (ONNX_INFO, "onnx", ["idle", "running", "anomaly"], 4),
        (MOCK_INFO, "mock", [], None),
    ],
)
def test_runner_info_per_backend(client, runner, info, backend, labels, expected):
    runner.handler = fake_runner(info)
    body = client.get("/api/v1/inference/runner").json()
    assert (body["backend"], body["labels"], body["expected_features"]) == (
        backend,
        labels,
        expected,
    )
    assert (body["inference_count"], body["last_latency_ms"]) == (12, 3.25)
    assert body["model"] == info["model"]


def test_runner_unreachable(client):
    r = client.get("/api/v1/inference/runner")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "runner_unavailable")


# ── POST /inference ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "info,values,backend",
    [
        (EIM_INFO, [1, 2, 3], "edge-impulse"),
        (ONNX_INFO, [1, 2, 3, 4.5], "onnx"),
        (MOCK_INFO, [7], "mock"),
    ],
)
def test_infer_each_backend(client, runner, info, values, backend):
    runner.handler = fake_runner(info)
    r = client.post("/api/v1/inference", json={"values": values, "device": "bench-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["backend"], body["mode"], body["device"]) == (backend, info["backend"], "bench-1")
    assert (body["label"], body["confidence"]) == ("running", 0.91)
    sent = json.loads(runner.requests[-1].content)
    assert sent == {"values": [float(v) for v in values], "device": "bench-1"}


@pytest.mark.parametrize("info,values", [(EIM_INFO, [1, 2]), (ONNX_INFO, [1, 2, 3])])
def test_wrong_feature_count_checked_before_the_runner(client, runner, info, values):
    runner.handler = fake_runner(info)
    r = client.post("/api/v1/inference", json={"values": values})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "wrong_feature_count")
    assert str(len(values)) in r.json()["error"]["message"]
    assert "/api/v1/infer" not in _paths(runner)


def test_model_failure_from_runner(client, runner):
    # The fixed runner answers 422 instead of a simulated result.
    failure = httpx.Response(422, json={"error": "ONNX inference failed: bad input"})
    runner.handler = fake_runner(MOCK_INFO | {"backend": "onnx", "model": {}}, infer=failure)
    r = client.post("/api/v1/inference", json={"values": [1, 2]})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "inference_failed")
    assert r.json()["error"]["message"] == "Edge runner: ONNX inference failed: bad input"


def test_simulated_result_from_a_real_backend_is_refused(client, runner):
    # Older runners fall back to mock when the model fails; that's not a classification.
    runner.handler = fake_runner(ONNX_INFO, infer=httpx.Response(200, json=_result("mock")))
    r = client.post("/api/v1/inference", json={"values": [1, 2, 3, 4]})
    assert (r.status_code, r.json()["error"]["code"]) == (502, "inference_failed")
    assert "simulated" in r.json()["error"]["message"]
    # The model description is fetched again next time (the runner may have restarted).
    client.post("/api/v1/inference", json={"values": [1, 2, 3, 4]})
    assert _paths(runner).count("/api/v1/info") == 2


def test_info_is_cached(client, runner):
    runner.handler = fake_runner(EIM_INFO)
    for _ in range(3):
        client.post("/api/v1/inference", json={"values": [1, 2, 3]})
    assert _paths(runner).count("/api/v1/info") == 1
    assert _paths(runner).count("/api/v1/infer") == 3


def test_runner_down(client):
    r = client.post("/api/v1/inference", json={"values": [1]})
    assert (r.status_code, r.json()["error"]["code"]) == (503, "runner_unavailable")


@pytest.mark.parametrize(
    "body",
    [
        {"values": []},
        {"values": [True, 1]},
        {"values": ["1"]},
        {"values": [1], "device": "a/b"},
        {"values": [1] * 50_001},
    ],
)
def test_infer_validation(client, runner, body):
    runner.handler = fake_runner(MOCK_INFO)
    assert client.post("/api/v1/inference", json=body).status_code == 422
    assert runner.requests == []


def test_infer_rejects_nan(client, runner):
    runner.handler = fake_runner(MOCK_INFO)
    r = client.post(
        "/api/v1/inference",
        content='{"values": [1, NaN]}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_inference_needs_operator(anon, admin, runner):
    runner.handler = fake_runner(MOCK_INFO)
    assert anon.post("/api/v1/inference", json={"values": [1]}).status_code == 401
    key = admin.post("/api/v1/devices", json={"id": "dev-1"}).json()["api_key"]
    token = anon.post("/api/v1/auth/token", json={"api_key": key}).json()["access_token"]
    r = anon.post(
        "/api/v1/inference", json={"values": [1]}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
    assert admin.post("/api/v1/inference", json={"values": [1]}).status_code == 200


# ── Stored results ────────────────────────────────────────────────────────────

RESULTS_CSV = (
    "#datatype,string,long,dateTime:RFC3339,string,string,string,double,double,double\r\n"
    ",result,table,_time,device,label,mode,confidence,anomaly_score,latency_ms\r\n"
    ",_result,0,2026-10-02T12:01:00Z,press-7,anomaly,onnx,0.97,0.88,4.1\r\n"
    ",_result,0,2026-10-02T12:00:00Z,press-7,idle,model,0.81,0.02,12.5\r\n"
    "\r\n"
)


def test_results(client, edge_project, influxdb):
    influxdb.handler = lambda request: httpx.Response(200, text=RESULTS_CSV)
    r = client.get(
        "/api/v1/inference/results",
        params={"device": "press-7", "backend": "edge-impulse", "limit": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is True
    assert body["results"][0] == {
        "time": "2026-10-02T12:01:00Z",
        "device": "press-7",
        "label": "anomaly",
        "backend": "onnx",
        "mode": "onnx",
        "confidence": 0.97,
        "anomaly_score": 0.88,
        "latency_ms": 4.1,
    }
    assert body["results"][1]["backend"] == "edge-impulse"
    flux = json.loads(influxdb.requests[-1].content)["query"]
    # The bucket comes from edge/.env (INFLUXDB_BUCKET_AI_EVENTS, "x" in the fixture).
    assert 'from(bucket: "x")' in flux
    assert 'r._measurement == "inference_result"' in flux
    assert 'r.mode == "model"' in flux  # edge-impulse is "model" in the runner's results
    assert "pivot(" in flux and "desc: true" in flux


def test_results_validation(client, edge_project, influxdb):
    assert client.get("/api/v1/inference/results", params={"backend": "tflite"}).status_code == 422
    assert client.get("/api/v1/inference/results", params={"start": "soon"}).status_code == 422
    assert influxdb.requests == []


def test_results_bucket_default_and_override(client, flat_project, influxdb, monkeypatch):
    influxdb.handler = lambda request: httpx.Response(200, text="")
    client.get("/api/v1/inference/results")
    assert 'from(bucket: "ai_events")' in json.loads(influxdb.requests[-1].content)["query"]
    monkeypatch.setenv("P4N4_API_INFLUXDB_AI_EVENTS_BUCKET", "edge_results")
    client.get("/api/v1/inference/results")
    assert 'from(bucket: "edge_results")' in json.loads(influxdb.requests[-1].content)["query"]


# ── Edge metrics and readiness ────────────────────────────────────────────────


def test_edge_metrics_include_inference_latency(client, edge_project, runner):
    runner.handler = fake_runner(ONNX_INFO)
    assert client.get("/api/v1/edge/metrics").json()["inference_ms"] == 3.25


def test_edge_metrics_without_runner(client, edge_project, runner):
    body = client.get("/api/v1/edge/metrics").json()
    assert "inference_ms" not in body and "cpu_percent" in body


def test_edge_metrics_skip_runner_without_edge_layer(client, flat_project, runner):
    runner.handler = fake_runner(ONNX_INFO)
    assert "inference_ms" not in client.get("/api/v1/edge/metrics").json()
    assert runner.requests == []


def test_edge_metrics_no_latency_yet(client, edge_project, runner):
    runner.handler = fake_runner(MOCK_INFO, health=HEALTH | {"last_latency_ms": None})
    assert "inference_ms" not in client.get("/api/v1/edge/metrics").json()


def test_ready_reports_runner(client, edge_project, runner, influxdb):
    influxdb.handler = lambda request: httpx.Response(200)
    r = client.get("/ready")
    assert r.status_code == 200  # the runner isn't required
    detail = r.json()["checks"]["edge_runner"]
    assert detail["ok"] is False and detail["required"] is False
    runner.handler = fake_runner(MOCK_INFO)
    assert client.get("/ready").json()["checks"]["edge_runner"] == {"ok": True, "required": False}
