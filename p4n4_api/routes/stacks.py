"""Stack endpoints: per-stack Compose service status (control and logs: routes/control.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from p4n4_lib import compose, layout
from pydantic import BaseModel

from p4n4_api import docker
from p4n4_api.deps import Project

router = APIRouter(prefix="/stacks", tags=["stacks"])


class Port(BaseModel):
    published: int
    target: int
    protocol: str


class Service(BaseModel):
    """One Compose service. p4n4-dashboard reads `name`, `state` and `health`: keep them."""

    name: str
    state: str  # Docker's state: running, exited, restarting, ...
    health: str  # healthy, unhealthy, starting, or "" without a healthcheck
    image: str | None
    version: str | None
    status: str | None
    exit_code: int | None
    ports: list[Port]
    started_at: str | None
    uptime_s: int | None


class Stack(BaseModel):
    name: str
    dir: str
    services: list[Service]
    running: int
    total: int


class Stacks(BaseModel):
    stacks: list[Stack]


def _image_version(image: str) -> str | None:
    """Tag of an image reference: `influxdb:2.7` → `2.7`, `localhost:5000/x` → None."""
    name = image.split("@", 1)[0]
    _, sep, tag = name.rpartition(":")
    return tag if sep and "/" not in tag else None


def _ports(svc: dict) -> list[Port]:
    """Published ports, deduplicated (Compose lists IPv4 and IPv6 bindings separately)."""
    seen = {
        (p.get("PublishedPort"), p.get("TargetPort"), p.get("Protocol", "tcp"))
        for p in svc.get("Publishers") or []
        if p.get("PublishedPort")
    }
    return [
        Port(published=pub, target=target, protocol=proto) for pub, target, proto in sorted(seen)
    ]


def _service(svc: dict, started: dict[str, datetime], now: datetime) -> Service:
    image = svc.get("Image") or None
    container_id = svc.get("ID", "")
    # `docker compose ps` may print a short ID; inspect always returns the full one.
    start = next(
        (t for cid, t in started.items() if container_id and cid.startswith(container_id)), None
    )
    running = svc.get("State") == "running"
    return Service(
        name=svc.get("Service") or svc.get("Name", "?"),
        state=svc.get("State") or "?",
        health=svc.get("Health") or "",
        image=image,
        version=_image_version(image) if image else None,
        status=svc.get("Status") or None,
        exit_code=None if running else svc.get("ExitCode"),
        ports=_ports(svc),
        started_at=start.isoformat() if start else None,
        uptime_s=int((now - start).total_seconds()) if start else None,
    )


def _stack_status(name: str, path: Path) -> Stack:
    raw = compose.ps(path)
    started = docker.started_at(
        [s["ID"] for s in raw if s.get("ID") and s.get("State") == "running"]
    )
    now = datetime.now(UTC)
    services = [_service(svc, started, now) for svc in raw]
    return Stack(
        name=name,
        dir=str(path),
        services=services,
        running=sum(1 for s in services if s.state == "running"),
        total=len(services),
    )


def stack_dirs(project: tuple[Path, dict]) -> list[tuple[str, Path]]:
    """(stack, directory) pairs in dependency order (iot before ai before edge)."""
    project_dir, data = project
    return layout.compose_dirs(project_dir, data.get("layers", []))


def find_stack(project: tuple[Path, dict], stack: str) -> Path:
    """A stack's directory, or 404."""
    for name, path in stack_dirs(project):
        if name == stack:
            return path
    raise HTTPException(status_code=404, detail=f"Stack '{stack}' not found in this project.")


@router.get("")
def stacks(project: Project, _: docker.DockerDaemon) -> Stacks:
    return Stacks(stacks=[_stack_status(name, path) for name, path in stack_dirs(project)])


@router.get("/{stack}")
def stack(stack: str, project: Project, _: docker.DockerDaemon) -> Stack:
    return _stack_status(stack, find_stack(project, stack))
