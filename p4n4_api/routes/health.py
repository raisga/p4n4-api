"""Liveness, readiness and version endpoints."""

from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from p4n4_api import __version__, ai, docker, edge_runner, influx
from p4n4_api.config import load_settings
from p4n4_api.deps import get_project
from p4n4_api.mqtt import bridge

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str


class Check(BaseModel):
    ok: bool
    detail: str | None = None
    # False for checks that don't make the API unavailable when they fail (e.g. MQTT: only
    # publishing and the live stream need it)
    required: bool = True


class Readiness(BaseModel):
    status: str  # "ready" or "unavailable"
    checks: dict[str, Check]


class Version(BaseModel):
    name: str
    version: str
    api: str
    p4n4_lib: str | None


@router.get("/health")
def health() -> Health:
    return Health(status="ok")


@router.get(
    "/ready",
    response_model=Readiness,
    responses={503: {"model": Readiness, "description": "A check failed"}},
)
async def ready() -> JSONResponse:
    """200 when the API can serve its endpoints, else 503: the project is found and Docker
    reachable (unless P4N4_API_DOCKER=off), plus InfluxDB (required) and MQTT (reported)
    when the project has the iot layer."""
    checks = {}
    project = None
    try:
        project = await asyncio.to_thread(get_project)
        checks["project"] = {"ok": True}
    except HTTPException as exc:
        checks["project"] = {"ok": False, "detail": exc.detail}
    error = await asyncio.to_thread(docker.daemon_error)
    checks["docker"] = {"ok": error is None, "detail": error}
    if not load_settings().docker_enabled:
        checks["docker"]["required"] = False  # turned off on purpose: status falls back
    if project is not None and "iot" in project[1].get("layers", []):
        error = await influx.ping(influx.config(project))
        checks["influxdb"] = {"ok": True} if error is None else {"ok": False, "detail": error}
        checks["mqtt"] = {"ok": bridge.connected, "detail": bridge.error, "required": False}
    if project is not None and "ai" in project[1].get("layers", []):
        cfg = ai.config(project)
        for name, error in zip(
            ("ollama", "letta"),
            await asyncio.gather(
                ai.ping(cfg.ollama_url, "/api/version"), ai.ping(cfg.letta_url, "/v1/health/")
            ),
            strict=True,
        ):
            checks[name] = {"ok": error is None, "detail": error, "required": False}
    if project is not None and "edge" in project[1].get("layers", []):
        try:
            await edge_runner.health(timeout=3)
            checks["edge_runner"] = {"ok": True, "required": False}
        except edge_runner.RunnerError as exc:
            checks["edge_runner"] = {"ok": False, "detail": str(exc), "required": False}
    ok = all(c["ok"] for c in checks.values() if c.get("required", True))
    body = Readiness(status="ready" if ok else "unavailable", checks=checks)
    content = body.model_dump(exclude_none=True)
    for check in content["checks"].values():
        if check["required"]:
            del check["required"]  # only shown on the optional ones
    return JSONResponse(status_code=200 if ok else 503, content=content)


def _lib_version() -> str | None:
    try:
        return version("p4n4-lib")
    except PackageNotFoundError:
        return None


version_router = APIRouter(tags=["health"])


@version_router.get("/version")
def api_version() -> Version:
    return Version(name="p4n4-api", version=__version__, api="v1", p4n4_lib=_lib_version())
