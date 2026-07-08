"""Project endpoints: manifest, layout, and validation."""

from __future__ import annotations

from fastapi import APIRouter
from p4n4_lib import layout
from p4n4_lib.validate import validate_project

from p4n4_api.deps import Project

router = APIRouter(prefix="/project", tags=["project"])


@router.get("")
def project_info(project: Project) -> dict:
    project_dir, data = project
    layers = data.get("layers", [])
    dirs = layout.compose_dirs(project_dir, layers)
    return {
        "project": data.get("project"),
        "schema_version": data.get("schema_version"),
        "created_at": data.get("created_at"),
        "root": str(project_dir),
        "layers": layers,
        "layout": "flat" if (project_dir / layout.COMPOSE_FILE).exists() else "multi",
        "stacks": [
            {"name": name, "dir": str(path), "relative_dir": str(path.relative_to(project_dir))}
            for name, path in dirs
        ],
    }


@router.get("/validate")
def project_validate(project: Project) -> dict:
    project_dir, data = project
    passed, errors = validate_project(project_dir, data)
    return {"ok": not errors, "passed": passed, "errors": errors}
