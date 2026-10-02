"""Liveness, readiness and version endpoints."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from p4n4_api import __version__, docker
from p4n4_api.deps import get_project

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> JSONResponse:
    """200 when the API can serve its endpoints (project found, Docker reachable), else 503."""
    checks = {}
    try:
        get_project()
        checks["project"] = {"ok": True}
    except HTTPException as exc:
        checks["project"] = {"ok": False, "detail": exc.detail}
    error = docker.daemon_error()
    checks["docker"] = {"ok": True} if error is None else {"ok": False, "detail": error}
    ok = all(c["ok"] for c in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "unavailable", "checks": checks},
    )


def _lib_version() -> str | None:
    try:
        return version("p4n4-lib")
    except PackageNotFoundError:
        return None


version_router = APIRouter(tags=["health"])


@version_router.get("/version")
def api_version() -> dict:
    return {"name": "p4n4-api", "version": __version__, "api": "v1", "p4n4_lib": _lib_version()}
