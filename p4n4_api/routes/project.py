"""Project endpoints: manifest, layout, validation, and what the dashboard shows of it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from p4n4_lib import layout
from p4n4_lib.validate import validate_project
from pydantic import BaseModel, Field, ValidationError

from p4n4_api.deps import Project

router = APIRouter(prefix="/project", tags=["project"])

# The Home tab's Markdown when the manifest doesn't name one (.p4n4.json dashboard.home).
HOME_FILE = "HOME.md"
HOME_MAX_BYTES = 256 * 1024
# The vision overlay carries every demo clip's boxes; the dashboard loads it once.
VISION_MAX_BYTES = 8 * 1024 * 1024


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


class Home(BaseModel):
    markdown: str


class VisionLive(BaseModel):
    """Where detections and verdicts stream as they happen: a ws(s) `url`, or a
    `port` and `path` on the host the dashboard is connected to."""

    url: str | None = None
    port: int | None = None
    path: str | None = None


class VisionRecording(BaseModel):
    """A video-file camera's boxes and behaviours, by the second of the clip."""

    duration: float = Field(gt=0)
    width: int | None = None
    height: int | None = None
    # [t, [[track, x1, y1, x2, y2, confidence(, kind)], …]]: boxes normalised to the frame;
    # the optional kind (an index into the overlay's `objects`, e.g. a team) colours the person
    boxes: list[list[Any]] = []
    # [start, end, track, behaviour, confidence]: seconds of the clip
    behaviors: list[list[Any]] = []
    # [t, [[kind, x1, y1, x2, y2, confidence], …]]: everything but people (a
    # garment, a bag), `kind` an index into the overlay's `objects`
    objects: list[list[Any]] = []


class VisionCamera(BaseModel):
    zones: list[str] = []
    recording: VisionRecording | None = None


class VisionAlertRule(BaseModel):
    """An alert the dashboard raises when a behaviour (in one of `zones`, if
    given) is on screen. `tone` is `review` (a risk) or `info` (an opportunity)."""

    code: str
    tone: Literal["info", "review"] = "info"
    behaviors: list[str]
    zones: list[str] | None = None
    title: dict[str, str]
    suggestion: dict[str, str] = {}


class VisionObjectKind(BaseModel):
    """A kind of object a recording's boxes can be (a store's garment
    category, say), with its label by language code. Its place in the list is its
    index in the boxes, and picks its colour on the dashboard."""

    id: str
    label: dict[str, str] = {}


class VisionOverlay(BaseModel):
    """What the Video tab draws over the cameras. Labels are by language code."""

    version: int = 1
    use_case: str | None = None
    source: dict[str, str | None] | None = None
    behaviors: dict[str, dict[str, str]] = {}
    zones: dict[str, dict[str, str]] = {}
    alerts: list[VisionAlertRule] = []
    objects: list[VisionObjectKind] = []
    cameras: dict[str, VisionCamera]
    live: VisionLive | None = None


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


@router.get(
    "/home", response_model=Home, responses={204: {"description": "The project has no home page."}}
)
def project_home(
    project: Project,
    lang: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")] = None,
) -> Home | Response:
    """The Markdown p4n4-dashboard shows at the top of its Home tab: the file
    `dashboard.home` names, or HOME.md. With `lang` (a language tag such as
    `es` or `es-MX`), HOME.es.md beside it comes first. 204 when there's none
    (missing, empty, too big or outside the project), so the dashboard just
    leaves the block out."""
    project_dir, data = project
    dashboard = data.get("dashboard")
    name = dashboard.get("home") if isinstance(dashboard, dict) else None
    root = project_dir.resolve()
    base = root / (name if isinstance(name, str) and name.strip() else HOME_FILE)
    candidates = [base]
    if lang:
        primary = lang.split("-")[0].lower()
        candidates.insert(0, base.with_name(f"{base.stem}.{primary}{base.suffix}"))
    for path in candidates:
        if (markdown := _read_home(root, path)) is not None:
            return Home(markdown=markdown)
    return Response(status_code=204)


def _read_home(root: Path, path: Path) -> str | None:
    """The file's Markdown, or None when it's missing, empty, too big or outside root."""
    path = path.resolve()
    if path.is_relative_to(root) and path.is_file() and path.stat().st_size <= HOME_MAX_BYTES:
        markdown = path.read_text(encoding="utf-8", errors="replace")
        if markdown.strip():
            return markdown
    return None


@router.get(
    "/vision",
    response_model=VisionOverlay,
    responses={204: {"description": "The project has no vision overlay."}},
)
def project_vision(project: Project) -> VisionOverlay | Response:
    """What p4n4-dashboard's Video tab draws over the cameras: the JSON file
    `dashboard.vision.file` names (labels, alert rules, and each camera's
    recorded boxes and behaviours), with the manifest's `dashboard.vision.live`
    added as `live` when there's a stream of detections to follow. 204 when
    there's none (no setting, or a missing, invalid, too big or outside file),
    so the dashboard just draws nothing. `p4n4 validate` says which."""
    project_dir, data = project
    dashboard = data.get("dashboard")
    vision = dashboard.get("vision") if isinstance(dashboard, dict) else None
    name = vision.get("file") if isinstance(vision, dict) else None
    if not (isinstance(name, str) and name.strip()):
        return Response(status_code=204)
    root = project_dir.resolve()
    path = (root / name).resolve()
    inside = path.is_relative_to(root) and path.is_file()
    if not inside or path.stat().st_size > VISION_MAX_BYTES:
        return Response(status_code=204)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return Response(status_code=204)
    if not isinstance(bundle, dict):
        return Response(status_code=204)
    live = vision.get("live")
    try:
        return VisionOverlay.model_validate(
            {**bundle, "live": live if isinstance(live, dict) else None}
        )
    except ValidationError:
        return Response(status_code=204)


@router.get("/validate")
def project_validate(project: Project) -> Validation:
    project_dir, data = project
    passed, errors = validate_project(project_dir, data)
    return Validation(ok=not errors, passed=passed, errors=errors)
