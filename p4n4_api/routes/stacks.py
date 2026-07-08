"""Stack endpoints: per-stack Compose service status."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from p4n4_lib import compose, layout

from p4n4_api.deps import Project

router = APIRouter(prefix="/stacks", tags=["stacks"])


def _stack_status(name: str, path: Path) -> dict:
    services = [
        {
            "name": svc.get("Service") or svc.get("Name", "?"),
            "state": svc.get("State", "?"),
            "health": svc.get("Health", ""),
        }
        for svc in compose.ps(path)
    ]
    return {
        "name": name,
        "dir": str(path),
        "services": services,
        "running": sum(1 for s in services if s["state"] == "running"),
        "total": len(services),
    }


@router.get("")
def stacks(project: Project) -> dict:
    project_dir, data = project
    dirs = layout.compose_dirs(project_dir, data.get("layers", []))
    return {"stacks": [_stack_status(name, path) for name, path in dirs]}


@router.get("/{stack}")
def stack(stack: str, project: Project) -> dict:
    project_dir, data = project
    dirs = layout.compose_dirs(project_dir, data.get("layers", []))
    for name, path in dirs:
        if name == stack:
            return _stack_status(name, path)
    raise HTTPException(status_code=404, detail=f"Stack '{stack}' not found in this project.")
