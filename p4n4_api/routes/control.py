"""Stack control (admin): up, down and restart as background jobs, and container logs."""

from __future__ import annotations

import asyncio
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from p4n4_lib import compose
from pydantic import BaseModel

from p4n4_api import audit, auth, docker, jobs
from p4n4_api.auth import CurrentUser
from p4n4_api.deps import Project
from p4n4_api.routes.stacks import find_stack, stack_dirs

router = APIRouter(prefix="/stacks", tags=["stack control"])
admin_only = [Depends(auth.require_role("admin"))]

ALL = "all"  # stack name meaning every stack, in dependency order (no layer has this name)
# Compose service names; also keeps a value starting with '-' from reaching Compose as a flag.
_SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
LOG_LINE_CHARS = 8000
HEARTBEAT_S = 15
LOGS_TIMEOUT_S = 30


class JobOut(BaseModel):
    id: str
    action: str
    stack: str
    service: str | None
    pull: bool
    requested_by: str
    status: str  # queued, running, succeeded, failed
    created_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    # Compose's output, most recent lines (empty in job lists)
    output: list[str]


class Logs(BaseModel):
    stack: str
    service: str | None
    lines: list[str]


def _services(path: Path) -> set[str]:
    """Services the stack defines (running or not), from `docker compose config`."""
    try:
        result = subprocess.run(
            [*compose.compose_cmd(), "config", "--services"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=LOGS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="docker compose config timed out.") from exc
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        detail = lines[-1] if lines else f"exit code {result.returncode}"
        raise HTTPException(status_code=502, detail=f"docker compose config failed: {detail}")
    return set(result.stdout.split())


def _check_service(path: Path, stack: str, service: str) -> None:
    if not _SERVICE.fullmatch(service) or service not in _services(path):
        raise HTTPException(status_code=404, detail=f"No service '{service}' in stack '{stack}'.")


def _queue(
    response: Response,
    user: CurrentUser,
    action: str,
    stack: str,
    targets: list[tuple[str, Path]],
    service: str | None = None,
    pull: bool = False,
) -> JobOut:
    job, created = jobs.submit(action, stack, targets, user.username, service=service, pull=pull)
    target = f"{stack}/{service}" if service else stack
    outcome = f"queued as job {job.id}" if created else f"already queued as job {job.id}"
    audit.record(user.username, f"stack.{action}", target, outcome)
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return JobOut(**job.snapshot())


@router.post("/{stack}/{action}", status_code=202, dependencies=admin_only)
def stack_action(
    stack: str,
    action: Literal["up", "down", "restart"],
    project: Project,
    _: docker.DockerDaemon,
    user: CurrentUser,
    response: Response,
    pull: bool = False,
) -> JobOut:
    """Start, stop or restart a stack (or `all` of them: up in dependency order, down in
    reverse), as a background job. Poll `Location` (`GET /api/v1/jobs/{id}`) for the outcome.

    `pull=true` (up only) pulls newer images first. `down` never removes volumes.
    """
    if pull and action != "up":
        raise HTTPException(status_code=422, detail="pull only applies to up.")
    targets = stack_dirs(project) if stack == ALL else [(stack, find_stack(project, stack))]
    if not targets:
        raise HTTPException(status_code=404, detail="This project has no stacks.")
    return _queue(response, user, action, stack, targets, pull=pull)


@router.post("/{stack}/services/{service}/restart", status_code=202, dependencies=admin_only)
def restart_service(
    stack: str,
    service: str,
    project: Project,
    _: docker.DockerDaemon,
    user: CurrentUser,
    response: Response,
) -> JobOut:
    """Restart one service of a stack, as a background job."""
    path = find_stack(project, stack)
    _check_service(path, stack, service)
    return _queue(response, user, "restart", stack, [(stack, path)], service=service)


# ── Logs ──────────────────────────────────────────────────────────────────────


def _logs_cmd(service: str | None, tail: int, follow: bool) -> list[str]:
    return [
        *compose.compose_cmd(),
        "logs",
        "--no-color",
        "--tail",
        str(tail),
        *(["--follow"] if follow else []),
        *([service] if service else []),
    ]


async def _spawn(cmd: list[str], cwd: Path) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        limit=2**20,  # longest line read whole; longer ones end the stream
    )


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.kill()
    await proc.wait()


def _line(raw: bytes) -> str:
    return raw.decode(errors="replace").rstrip("\r\n")[:LOG_LINE_CHARS]


async def follow_events(cmd: list[str], cwd: Path) -> AsyncIterator[str]:
    """Server-sent events: one `data:` per log line, a comment every HEARTBEAT_S seconds so
    proxies keep the connection open, and an `end` event with Compose's exit code.

    When the client disconnects, the response stops iterating this generator, and the
    `finally` kills Compose: nothing keeps following logs for nobody.
    """
    proc = await _spawn(cmd, cwd)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), HEARTBEAT_S)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            except ValueError:  # a line longer than the read limit
                yield "event: error\ndata: log line too long; stream stopped\n\n"
                break
            if not raw:
                break
            yield f"data: {_line(raw)}\n\n"
        await _stop(proc)
        yield f"event: end\ndata: {proc.returncode}\n\n"
    finally:
        await _stop(proc)


@router.get(
    "/{stack}/logs",
    dependencies=admin_only,
    responses={200: {"content": {"text/event-stream": {}}, "description": "With follow=true"}},
)
async def stack_logs(
    stack: str,
    project: Project,
    _: docker.DockerDaemon,
    service: str | None = None,
    tail: Annotated[int, Query(ge=1, le=5000)] = 200,
    follow: bool = False,
) -> Logs:
    """A stack's container logs (admin only: logs can contain secrets).

    The last `tail` lines as JSON; with `follow=true`, those lines and then new ones as they
    come, as server-sent events (`text/event-stream`) until the client disconnects.
    """
    path = find_stack(project, stack)
    if service is not None:
        await asyncio.to_thread(_check_service, path, stack, service)
    cmd = _logs_cmd(service, tail, follow)
    if follow:
        return StreamingResponse(
            follow_events(cmd, path),
            media_type="text/event-stream",
            # X-Accel-Buffering: nginx (the dashboard's proxy) would otherwise hold events back
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    proc = await _spawn(cmd, path)
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), LOGS_TIMEOUT_S)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="docker compose logs timed out.") from exc
    finally:
        await _stop(proc)
    lines = [_line(raw) for raw in out.splitlines()]
    if proc.returncode != 0:
        last = lines[-1] if lines else f"exit code {proc.returncode}"
        raise HTTPException(status_code=502, detail=f"docker compose logs failed: {last}")
    return Logs(stack=stack, service=service, lines=lines)
