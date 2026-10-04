"""Inference on the edge runner (Edge Impulse, ONNX or mock backend), and its stored results."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, field_validator

from p4n4_api import auth, edge_runner, influx
from p4n4_api.deps import OptionalProject
from p4n4_api.errors import ApiError

router = APIRouter(prefix="/inference", tags=["inference"])
operator_only = [Depends(auth.require_role("operator"))]

Backend = Literal["edge-impulse", "onnx", "mock"]
_MODES = {backend: mode for mode, backend in edge_runner.BACKENDS.items()}


class RunnerInfo(BaseModel):
    backend: str  # edge-impulse, onnx or mock (the runner's MODEL_BACKEND outcome)
    model_file: str | None
    # What the backend reports: Edge Impulse project, labels and input_features_count; ONNX
    # input_name and input_shape
    model: dict[str, Any]
    labels: list[str]
    # Values POST /inference needs, when the model says (None: any number)
    expected_features: int | None
    inference_count: int | None
    last_inference_at: str | None
    last_latency_ms: float | None
    mqtt_connected: bool | None
    influxdb_ok: bool | None


class InferenceRequest(BaseModel):
    values: list[float] = Field(min_length=1, max_length=50_000)
    # Recorded in the result only (the runner doesn't publish or store API inferences)
    device: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")] = "api"

    @field_validator("values", mode="before")
    @classmethod
    def _numbers_only(cls, values: object) -> object:
        if isinstance(values, list):
            for v in values:
                if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                    raise ValueError("values must be finite numbers")
        return values


class InferenceResult(BaseModel):
    device: str
    timestamp: str
    label: str
    confidence: float
    anomaly_score: float
    latency_ms: float
    backend: str
    mode: str  # the runner's own name for the backend (model = Edge Impulse)


class StoredResult(BaseModel):
    time: str
    device: str | None
    label: str | None
    backend: str | None
    mode: str | None
    confidence: float | None
    anomaly_score: float | None
    latency_ms: float | None


class StoredResults(BaseModel):
    results: list[StoredResult]
    truncated: bool


def _runner_error(exc: edge_runner.RunnerError) -> HTTPException:
    if exc.status is None:
        return ApiError(503, "runner_unavailable", str(exc))
    if exc.status in (400, 422):
        return ApiError(422, "inference_failed", str(exc))
    return ApiError(502, "upstream_error", str(exc))


def _backend(mode: str | None) -> str | None:
    return edge_runner.BACKENDS.get(mode, mode) if mode else None


@router.get("/runner")
async def runner_info() -> RunnerInfo:
    """The runner's active backend and model, and its pipeline counters."""
    try:
        info = await edge_runner.info(fresh=True)
        health = await edge_runner.health()
    except edge_runner.RunnerError as exc:
        raise _runner_error(exc) from exc
    return RunnerInfo(
        backend=_backend(info.get("backend")) or "unknown",
        model_file=info.get("model_file"),
        model=info.get("model") or {},
        labels=(info.get("model") or {}).get("labels") or info.get("labels") or [],
        expected_features=edge_runner.expected_features(info),
        inference_count=health.get("inference_count"),
        last_inference_at=health.get("last_inference_at"),
        last_latency_ms=health.get("last_latency_ms"),
        mqtt_connected=health.get("mqtt_connected"),
        influxdb_ok=health.get("influxdb_ok"),
    )


@router.post("", dependencies=operator_only)
async def infer(body: InferenceRequest) -> InferenceResult:
    """Classify one feature vector with the runner's model (Edge Impulse or ONNX; simulated
    in mock mode). The result is returned only: not published to MQTT or stored."""
    try:
        info = await edge_runner.info()
        expected = edge_runner.expected_features(info)
        if expected is not None and len(body.values) != expected:
            raise ApiError(
                422,
                "wrong_feature_count",
                f"The {_backend(info.get('backend'))} model takes {expected} values; "
                f"got {len(body.values)}.",
            )
        result = await edge_runner.infer(body.values, body.device)
    except edge_runner.RunnerError as exc:
        raise _runner_error(exc) from exc
    if result.get("mode") == "mock" and info.get("backend") not in (None, "mock"):
        # Runners before p4n4-edge's fix answer a model failure with a simulated result.
        edge_runner.clear_cache()
        raise ApiError(
            502,
            "inference_failed",
            f"The runner's {_backend(info.get('backend'))} model failed on this input and "
            "returned a simulated result instead; see the runner's log.",
        )
    return InferenceResult(**result, backend=_backend(result.get("mode")) or "unknown")


@router.get("/results")
async def results(
    project: OptionalProject,
    device: str | None = None,
    label: str | None = None,
    backend: Backend | None = None,
    start: str = "-1h",
    stop: str | None = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 100,
) -> StoredResults:
    """Inference results the runner's pipeline stored (InfluxDB `ai_events`,
    `inference_result`), newest first. `start`/`stop` as for `GET /telemetry`."""
    cfg = influx.config(project)
    try:
        flux = influx.build_results_query(
            cfg.ai_events_bucket,
            start=start,
            stop=stop,
            device=device,
            label=label,
            mode=_MODES[backend] if backend else None,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        rows = await influx.query(cfg, flux)
    except influx.InfluxError as exc:
        raise influx.http_error(exc) from exc
    items = [
        StoredResult(
            time=row["_time"],
            device=row.get("device"),
            label=row.get("label"),
            backend=_backend(row.get("mode")),
            mode=row.get("mode"),
            confidence=row.get("confidence"),
            anomaly_score=row.get("anomaly_score"),
            latency_ms=row.get("latency_ms"),
        )
        for row in rows
    ]
    return StoredResults(results=items, truncated=len(items) >= limit)
