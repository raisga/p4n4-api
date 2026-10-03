"""Edge endpoints: system metrics of the host running the API (the edge device)."""

from __future__ import annotations

import asyncio
import time

import psutil
from fastapi import APIRouter
from pydantic import BaseModel

from p4n4_api import edge_runner
from p4n4_api.config import load_settings
from p4n4_api.deps import OptionalProject

router = APIRouter(prefix="/edge", tags=["edge"])

# CPU/SoC temperature sensors, most specific first: Raspberry Pi, Intel, AMD, generic SoCs.
# Board sensors such as acpitz often report a fixed or ambient value, so they're never used.
_TEMP_SENSORS = ("cpu_thermal", "coretemp", "k10temp", "zenpower", "soc_thermal", "cpu-thermal")

# cpu_percent(interval=None) compares against the previous call, and the first call
# returns a meaningless 0.0. Prime it at import so the first request gets a real value.
psutil.cpu_percent(interval=None)


def _temp_c() -> float | None:
    """CPU/SoC temperature, or None when no known sensor exists (macOS, Windows, VMs)."""
    sensors_temperatures = getattr(psutil, "sensors_temperatures", None)
    if sensors_temperatures is None:
        return None
    try:
        sensors = sensors_temperatures()
    except OSError:
        return None
    names = [n for n in _TEMP_SENSORS if sensors.get(n)]
    names += [n for n in sensors if sensors[n] and ("cpu" in n or "soc" in n) and n not in names]
    return round(sensors[names[0]][0].current, 1) if names else None


def _disk_percent() -> float | None:
    """Usage of P4N4_API_DISK_PATH, or None when it doesn't exist (e.g. a volume not mounted)."""
    try:
        return psutil.disk_usage(str(load_settings().disk_path)).percent
    except OSError:
        return None


class EdgeMetrics(BaseModel):
    """p4n4-dashboard's edge metrics contract (dashboard/README.md#edge-metrics-contract).
    Only the percentages are required; unknown values are omitted, never sent as null."""

    cpu_percent: float
    mem_percent: float
    mem_used_mb: int | None = None
    mem_total_mb: int | None = None
    disk_percent: float | None = None
    temp_c: float | None = None
    uptime_s: int | None = None
    load: list[float] | None = None  # 1, 5 and 15 minute load averages
    # The edge runner's last pipeline inference latency (Edge Impulse or ONNX)
    inference_ms: float | None = None


RUNNER_TIMEOUT_S = 0.5  # metrics are polled often: never wait long for the runner


async def inference_ms_from_runner(project: tuple | None) -> float | None:
    if project is None or "edge" not in project[1].get("layers", []):
        return None
    try:
        latency = (await edge_runner.health(RUNNER_TIMEOUT_S)).get("last_latency_ms")
    except edge_runner.RunnerError:
        return None
    return float(latency) if isinstance(latency, int | float) else None


@router.get("/metrics", response_model_exclude_none=True)
async def metrics(project: OptionalProject) -> EdgeMetrics:
    """Snapshot of the host's CPU, memory, disk, temperature, uptime and load, plus the
    edge runner's last inference latency when the project has the edge layer."""
    host, inference_ms = await asyncio.gather(
        asyncio.to_thread(host_metrics), inference_ms_from_runner(project)
    )
    return host.model_copy(update={"inference_ms": inference_ms})


def host_metrics() -> EdgeMetrics:
    mem = psutil.virtual_memory()
    return EdgeMetrics(
        cpu_percent=psutil.cpu_percent(interval=None),
        mem_percent=mem.percent,
        mem_used_mb=round(mem.used / 2**20),
        mem_total_mb=round(mem.total / 2**20),
        disk_percent=_disk_percent(),
        temp_c=_temp_c(),
        uptime_s=int(time.time() - psutil.boot_time()),
        load=[round(x, 2) for x in psutil.getloadavg()],
    )
