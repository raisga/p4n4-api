"""Telemetry: devices send readings, operators query history and follow live readings."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints, field_validator

from p4n4_api import auth, db, devices, influx
from p4n4_api.auth import CurrentUser
from p4n4_api.deps import OptionalProject
from p4n4_api.mqtt import RESERVED_KEYS, bridge

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
readers = [Depends(auth.require_role("normie"))]

HEARTBEAT_S = 15
LAST_SEEN_EVERY = timedelta(minutes=1)

# Becomes an MQTT topic level and an InfluxDB tag: no '/', and none of MQTT's '+' or '#'.
SensorName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
FieldValue = bool | int | float | Annotated[str, StringConstraints(max_length=1024)]


class Reading(BaseModel):
    sensor: SensorName
    # As in an MQTT payload: {"value": 23.5, "unit": "C"}
    fields: dict[str, FieldValue] = Field(min_length=1, max_length=100)
    # When it was measured (RFC 3339, or Unix seconds); default: when it arrives
    ts: datetime | None = None

    @field_validator("fields")
    @classmethod
    def _check_fields(cls, fields: dict) -> dict:
        for key, value in fields.items():
            if not key or len(key) > 64 or key in RESERVED_KEYS or key.startswith("_"):
                raise ValueError(
                    f"field name {key!r} is not allowed (1-64 characters, not starting with "
                    f"'_', not one of {', '.join(sorted(RESERVED_KEYS))})"
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"field {key!r} is not a finite number")
        return fields


class Batch(BaseModel):
    readings: list[Reading] = Field(min_length=1, max_length=1000)


class IngestResult(BaseModel):
    stored: int
    # Published to MQTT for Node-RED flows and live streams (fewer when the broker is down;
    # the readings are stored either way)
    published: int


class Point(BaseModel):
    time: str
    device: str | None
    sensor: str | None
    field: str
    value: float | int | bool | str | None


class Points(BaseModel):
    points: list[Point]
    # True when `limit` points came back: there may be more
    truncated: bool


def _measured_at(ts: datetime | None, now: datetime) -> datetime:
    """The reading's own time (naive ones taken as UTC), or now."""
    if ts is None:
        return now
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _touch_last_seen(device_id: str) -> None:
    """Throttled: at most one database write a minute per device, not one per batch."""
    now = datetime.now(UTC)
    with db.connect() as conn:
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ? "
            "AND (last_seen_at IS NULL OR last_seen_at < ?)",
            (
                now.isoformat(timespec="seconds"),
                device_id,
                (now - LAST_SEEN_EVERY).isoformat(timespec="seconds"),
            ),
        )


@router.post("", status_code=201, dependencies=[Depends(auth.require_role("device"))])
async def ingest(body: Batch, device: CurrentUser, project: OptionalProject) -> IngestResult:
    """Store a batch of readings from the signed-in device (device role only).

    Readings land in InfluxDB as the IoT stack's Node-RED flow stores MQTT readings
    (`sensor_data`, tagged `device` and `sensor`), with the device's own timestamps. `201`
    means they're stored. Each is then published to `sensors/{device}/{sensor}` for Node-RED
    flows and live streams, marked so Node-RED doesn't store it again.
    """
    device_id = device.username.removeprefix(devices.SUBJECT_PREFIX)
    now = datetime.now(UTC)
    stamped = [(r, _measured_at(r.ts, now)) for r in body.readings]
    lines = [influx.line(device_id, r.sensor, r.fields, at) for r, at in stamped]
    try:
        await influx.write(influx.config(project), lines)
    except influx.InfluxError as exc:
        raise influx.http_error(exc) from exc
    await asyncio.to_thread(_touch_last_seen, device_id)
    published = 0
    for reading, at in stamped:
        ts = at.astimezone(UTC).isoformat(timespec="milliseconds")
        published += await bridge.publish(device_id, reading.sensor, reading.fields, ts)
    return IngestResult(stored=len(lines), published=published)


@router.get("", dependencies=readers)
async def query(
    project: OptionalProject,
    device: str | None = None,
    sensor: str | None = None,
    field: str | None = None,
    start: str = "-1h",
    stop: str | None = None,
    every: str | None = None,
    agg: Literal["mean", "median", "min", "max", "sum", "count", "first", "last"] = "mean",
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> Points:
    """Stored readings, oldest first.

    `start`/`stop`: a duration back from now (`-1h`, `-7d`) or an RFC 3339 time. `every`
    (e.g. `1m`) averages into windows, or applies `agg`. Filters match the `device` and
    `sensor` tags and the field name. Built into Flux from these values; raw Flux isn't
    accepted.
    """
    cfg = influx.config(project)
    try:
        flux = influx.build_query(
            cfg.bucket,
            start=start,
            stop=stop,
            device=device,
            sensor=sensor,
            field=field,
            every=every,
            agg=agg,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        rows = await influx.query(cfg, flux)
    except influx.InfluxError as exc:
        raise influx.http_error(exc) from exc
    points = [
        Point(
            time=row["_time"],
            device=row.get("device"),
            sensor=row.get("sensor"),
            field=row["_field"],
            value=row.get("_value"),
        )
        for row in rows
    ]
    return Points(points=points, truncated=len(points) >= limit)


async def stream_events(device: str | None, sensor: str | None) -> AsyncIterator[str]:
    """`status` first (is MQTT connected), then a `reading` per message, `dropped` when a
    slow client lost some, and a keep-alive comment every HEARTBEAT_S seconds."""
    async with bridge.subscribe(device, sensor) as subscriber:
        yield f"event: status\ndata: {json.dumps({'mqtt': bridge.connected})}\n\n"
        reported = 0
        while True:
            try:
                reading = await asyncio.wait_for(subscriber.queue.get(), HEARTBEAT_S)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if subscriber.dropped != reported:
                reported = subscriber.dropped
                yield f"event: dropped\ndata: {reported}\n\n"
            yield f"event: reading\ndata: {json.dumps(reading)}\n\n"


@router.get(
    "/stream",
    dependencies=readers,
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Live readings"}},
)
async def stream(device: str | None = None, sensor: str | None = None) -> StreamingResponse:
    """Live readings as server-sent events, from MQTT `sensors/+/+` (devices' own and
    ingested ones), optionally for one device and/or sensor."""
    return StreamingResponse(
        stream_events(device, sensor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
