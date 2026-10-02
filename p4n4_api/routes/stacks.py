"""Stack endpoints: per-stack Compose service status."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from p4n4_lib import compose, layout

from p4n4_api import docker
from p4n4_api.deps import Project

router = APIRouter(prefix="/stacks", tags=["stacks"])


def _image_version(image: str) -> str | None:
    """Tag of an image reference: `influxdb:2.7` → `2.7`, `localhost:5000/x` → None."""
    name = image.split("@", 1)[0]
    _, sep, tag = name.rpartition(":")
    return tag if sep and "/" not in tag else None


def _ports(svc: dict) -> list[dict]:
    """Published ports, deduplicated (Compose lists IPv4 and IPv6 bindings separately)."""
    seen = {
        (p.get("PublishedPort"), p.get("TargetPort"), p.get("Protocol", "tcp"))
        for p in svc.get("Publishers") or []
        if p.get("PublishedPort")
    }
    return [
        {"published": pub, "target": target, "protocol": proto}
        for pub, target, proto in sorted(seen)
    ]


def _service(svc: dict, started: dict[str, datetime], now: datetime) -> dict:
    image = svc.get("Image") or None
    container_id = svc.get("ID", "")
    # `docker compose ps` may print a short ID; inspect always returns the full one.
    start = next(
        (t for cid, t in started.items() if container_id and cid.startswith(container_id)), None
    )
    running = svc.get("State") == "running"
    return {
        "name": svc.get("Service") or svc.get("Name", "?"),
        "state": svc.get("State", "?"),
        "health": svc.get("Health", ""),
        "image": image,
        "version": _image_version(image) if image else None,
        "status": svc.get("Status") or None,
        "exit_code": None if running else svc.get("ExitCode"),
        "ports": _ports(svc),
        "started_at": start.isoformat() if start else None,
        "uptime_s": int((now - start).total_seconds()) if start else None,
    }


def _stack_status(name: str, path: Path) -> dict:
    raw = compose.ps(path)
    started = docker.started_at(
        [s["ID"] for s in raw if s.get("ID") and s.get("State") == "running"]
    )
    now = datetime.now(UTC)
    services = [_service(svc, started, now) for svc in raw]
    return {
        "name": name,
        "dir": str(path),
        "services": services,
        "running": sum(1 for s in services if s["state"] == "running"),
        "total": len(services),
    }


@router.get("")
def stacks(project: Project, _: docker.DockerDaemon) -> dict:
    project_dir, data = project
    dirs = layout.compose_dirs(project_dir, data.get("layers", []))
    return {"stacks": [_stack_status(name, path) for name, path in dirs]}


@router.get("/{stack}")
def stack(stack: str, project: Project, _: docker.DockerDaemon) -> dict:
    project_dir, data = project
    dirs = layout.compose_dirs(project_dir, data.get("layers", []))
    for name, path in dirs:
        if name == stack:
            return _stack_status(name, path)
    raise HTTPException(status_code=404, detail=f"Stack '{stack}' not found in this project.")
