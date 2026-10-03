"""Edge endpoints: system metrics of the host running the API (the edge device)."""

from __future__ import annotations

import time

import psutil
from fastapi import APIRouter
from pydantic import BaseModel

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
    inference_ms: float | None = None  # from the Edge Impulse runner, once M6 exists


@router.get("/metrics", response_model_exclude_none=True)
def metrics() -> EdgeMetrics:
    """Snapshot of the host's CPU, memory, disk, temperature, uptime and load."""
    mem = psutil.virtual_memory()
    return EdgeMetrics(
        cpu_percent=psutil.cpu_percent(interval=None),
        mem_percent=mem.percent,
        mem_used_mb=round(mem.used / 2**20),
        mem_total_mb=round(mem.total / 2**20),
        disk_percent=psutil.disk_usage("/").percent,
        temp_c=_temp_c(),
        uptime_s=int(time.time() - psutil.boot_time()),
        load=[round(x, 2) for x in psutil.getloadavg()],
    )
