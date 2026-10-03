"""A short, plain-text summary of the system for AI prompts (`include_status` on chats)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from p4n4_api import docker
from p4n4_api.routes.edge import host_metrics, inference_ms_from_runner
from p4n4_api.routes.stacks import stack_dirs, stack_status


def _stacks(project: tuple) -> list[str]:
    error = docker.daemon_error()
    if error:
        return [f"- Stacks: unknown (Docker unavailable: {error})"]
    lines = []
    for name, path in stack_dirs(project):
        stack = stack_status(name, path)
        line = f"- Stack {name}: {stack.running}/{stack.total} services running"
        problems = [
            f"{s.name} {s.state}" + (f" (exit {s.exit_code})" if s.exit_code else "")
            for s in stack.services
            if s.state != "running"
        ] + [f"{s.name} {s.health}" for s in stack.services if s.health == "unhealthy"]
        lines.append(line + (f"; {', '.join(problems)}" if problems else ""))
    return lines or ["- Stacks: none in this project"]


def _edge(metrics, inference_ms: float | None) -> str:
    parts = [f"CPU {metrics.cpu_percent:.0f}%", f"memory {metrics.mem_percent:.0f}%"]
    if metrics.disk_percent is not None:
        parts.append(f"disk {metrics.disk_percent:.0f}%")
    if metrics.temp_c is not None:
        parts.append(f"CPU temperature {metrics.temp_c:.1f} °C")
    if metrics.load:
        parts.append(f"load {metrics.load[0]:.2f}")
    if inference_ms is not None:
        parts.append(f"last inference {inference_ms:.1f} ms")
    return "- Edge host: " + ", ".join(parts)


async def system_status(project: tuple | None) -> str:
    """What an assistant should know to answer "how is my system doing?"."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [f"Current p4n4 system status ({now}):"]
    if project is not None:
        lines += await asyncio.to_thread(_stacks, project)
    metrics, inference_ms = await asyncio.gather(
        asyncio.to_thread(host_metrics), inference_ms_from_runner(project)
    )
    lines.append(_edge(metrics, inference_ms))
    return "\n".join(lines)
