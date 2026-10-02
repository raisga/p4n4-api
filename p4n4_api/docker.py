"""Docker daemon helpers that p4n4_lib.compose doesn't cover."""

from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException
from p4n4_lib import compose

_TIMEOUT_S = 5


def daemon_error() -> str | None:
    """Why Docker (daemon or Compose) can't be used, or None when it can.

    `p4n4_lib.compose.ps` ignores Compose's exit code, so with the daemon down every
    stack would look empty rather than failing. Check the daemon explicitly first.
    """
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return "Docker CLI not found on the API host."
    except subprocess.TimeoutExpired:
        return f"Docker daemon did not answer within {_TIMEOUT_S} s."
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        return lines[-1] if lines else f"docker version exited with {result.returncode}."
    try:
        compose.compose_cmd()
    except compose.ComposeNotFoundError as exc:
        return str(exc)
    return None


def require_daemon() -> None:
    """Dependency: 503 when the Docker daemon is unreachable."""
    error = daemon_error()
    if error:
        raise HTTPException(status_code=503, detail=f"Docker is unavailable: {error}")


DockerDaemon = Annotated[None, Depends(require_daemon)]


def started_at(container_ids: list[str]) -> dict[str, datetime]:
    """Start time of each running container, keyed by full container ID.

    Best-effort: returns {} if `docker inspect` fails, so stack status still works.
    """
    if not container_ids:
        return {}
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Id}} {{.State.Running}} {{.State.StartedAt}}",
                *container_ids,
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    times = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[1] != "true":
            continue
        try:
            # Docker uses RFC 3339 with nanoseconds; fromisoformat truncates to microseconds.
            times[parts[0]] = datetime.fromisoformat(parts[2])
        except ValueError:
            continue
    return times
