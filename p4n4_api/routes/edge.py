"""Edge endpoints: system metrics of the host running the API (the edge device)."""

from __future__ import annotations

import time

import psutil
from fastapi import APIRouter

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


@router.get("/metrics")
def metrics() -> dict:
    """Snapshot in the dashboard's edge metrics contract; unknown fields are omitted."""
    mem = psutil.virtual_memory()
    body = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "mem_percent": mem.percent,
        "mem_used_mb": round(mem.used / 2**20),
        "mem_total_mb": round(mem.total / 2**20),
        "disk_percent": psutil.disk_usage("/").percent,
        "uptime_s": int(time.time() - psutil.boot_time()),
        "load": [round(x, 2) for x in psutil.getloadavg()],
    }
    temp = _temp_c()
    if temp is not None:
        body["temp_c"] = temp
    return body
