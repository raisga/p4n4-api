"""Project endpoints: manifest, layout, and validation."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from p4n4_lib import layout
from p4n4_lib.validate import validate_project
from pydantic import BaseModel, Field

from p4n4_api.deps import Project

router = APIRouter(prefix="/project", tags=["project"])


class StackDir(BaseModel):
    name: str
    dir: str
    relative_dir: str


class ProjectInfo(BaseModel):
    project: str | None
    schema_version: int | None
    created_at: str | None
    root: str
    layers: list[str]
    # Where the project came from, and how p4n4-dashboard should present it (objects, both
    # optional). Passed through as-is: p4n4_lib checks them (GET /project/validate), and a
    # strict type here would turn a hand-edited manifest into a 500 instead of an error there.
    template: Any = Field(None, json_schema_extra={"type": ["object", "null"]})
    dashboard: Any = Field(None, json_schema_extra={"type": ["object", "null"]})
    layout: Literal["flat", "multi"]
    stacks: list[StackDir]


class Validation(BaseModel):
    ok: bool
    passed: list[str]
    errors: list[str]


@router.get("")
def project_info(project: Project) -> ProjectInfo:
    project_dir, data = project
    layers = data.get("layers", [])
    dirs = layout.compose_dirs(project_dir, layers)
    return ProjectInfo(
        project=data.get("project"),
        schema_version=data.get("schema_version"),
        created_at=data.get("created_at"),
        root=str(project_dir),
        layers=layers,
        template=data.get("template"),
        dashboard=data.get("dashboard"),
        layout="flat" if (project_dir / layout.COMPOSE_FILE).exists() else "multi",
        stacks=[
            StackDir(name=name, dir=str(path), relative_dir=str(path.relative_to(project_dir)))
            for name, path in dirs
        ],
    )


@router.get("/validate")
def project_validate(project: Project) -> Validation:
    project_dir, data = project
    passed, errors = validate_project(project_dir, data)
    return Validation(ok=not errors, passed=passed, errors=errors)
