"""InfluxDB 2 over its HTTP API: write line protocol, run Flux built from checked parameters.

Points use the schema the IoT stack's Node-RED flow writes for `sensors/{device}/{sensor}`
(stacks/iot config/node-red/flows/flows.json), so API and MQTT readings sit side by side:
measurement `sensor_data`, tags `device` and `sensor`, one field per payload key.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException

from p4n4_api.config import load_settings
from p4n4_api.deps import layer_env
from p4n4_api.errors import ApiError

MEASUREMENT = "sensor_data"
TIMEOUT_S = 10
# Swapped in by tests (httpx.MockTransport); None means real HTTP.
transport: httpx.AsyncBaseTransport | None = None


class InfluxError(Exception):
    """InfluxDB unreachable, or it refused the request (`status` is its HTTP status)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotConfigured(InfluxError):
    """No token: neither P4N4_API_INFLUXDB_TOKEN nor the project's iot/.env has one."""


def http_error(exc: InfluxError) -> HTTPException:
    """The API error for an InfluxDB failure."""
    if isinstance(exc, NotConfigured):
        return ApiError(503, "influxdb_not_configured", str(exc))
    if exc.status is None:
        return ApiError(503, "influxdb_unavailable", str(exc))
    if exc.status in (400, 422):
        return ApiError(422, "influxdb_rejected", str(exc))
    return ApiError(502, "upstream_error", str(exc))


@dataclass(frozen=True)
class Config:
    url: str
    token: str | None
    org: str
    bucket: str
    # The edge runner's inference results
    ai_events_bucket: str = "ai_events"


def config(project: tuple | None) -> Config:
    """Settings first, then the project's iot/.env (the IoT stack's own; edge/.env for the
    AI events bucket), then defaults."""
    settings = load_settings()
    iot, edge = layer_env(project, "iot"), layer_env(project, "edge")
    return Config(
        url=settings.influxdb_url.rstrip("/"),
        token=settings.influxdb_token or iot.get("INFLUXDB_TOKEN") or None,
        org=settings.influxdb_org or iot.get("INFLUXDB_ORG") or "ming",
        bucket=settings.influxdb_bucket or iot.get("INFLUXDB_BUCKET") or "raw_telemetry",
        ai_events_bucket=settings.influxdb_ai_events_bucket
        or edge.get("INFLUXDB_BUCKET_AI_EVENTS")
        or iot.get("INFLUXDB_BUCKET_AI_EVENTS")
        or "ai_events",
    )


# ── Line protocol ─────────────────────────────────────────────────────────────


def _clean(s: str) -> str:
    return re.sub(r"[\r\n]+", " ", s)


def _key(s: str) -> str:
    """Tag keys, tag values and field keys: escape commas, equals signs and spaces."""
    return re.sub(r"([\\,= ])", r"\\\1", _clean(s))


def _field_value(value: float | int | str | bool) -> str | None:
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int | float):
        # Always a float, as Node-RED writes (JavaScript has no ints): a field must keep one
        # type, and "23i" next to Node-RED's "23" would be refused as a conflict.
        return repr(float(value)) if math.isfinite(value) else None
    return '"' + re.sub(r'([\\"])', r"\\\1", _clean(value)) + '"'


def line(device: str, sensor: str, fields: dict, at: datetime) -> str | None:
    """One `sensor_data` point, or None when no field has a storable value."""
    encoded = [
        f"{_key(k)}={v}" for k, raw in fields.items() if (v := _field_value(raw)) is not None
    ]
    if not encoded:
        return None
    ms = int(at.timestamp() * 1000)
    return f"{MEASUREMENT},device={_key(device)},sensor={_key(sensor)} {','.join(encoded)} {ms}"


# ── Flux ──────────────────────────────────────────────────────────────────────

AGGREGATES = ("mean", "median", "min", "max", "sum", "count", "first", "last")
_DURATION = re.compile(r"-?\d{1,6}(ms|s|m|h|d|w)")


def flux_string(value: str) -> str:
    """A Flux string literal. Values only ever enter queries this way."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{escaped}"'


def flux_time(value: str) -> str:
    """A relative duration (`-1h`) or an absolute time (RFC 3339), as Flux; ValueError if
    it's neither."""
    if _DURATION.fullmatch(value):
        return value
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{value!r} is neither a duration like -1h nor an RFC 3339 time") from None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def flux_duration(value: str) -> str:
    if not _DURATION.fullmatch(value) or value.startswith("-"):
        raise ValueError(f"{value!r} is not a duration like 1m or 1h")
    return value


def build_query(
    bucket: str,
    *,
    start: str,
    stop: str | None,
    device: str | None,
    sensor: str | None,
    field: str | None,
    every: str | None,
    agg: str,
    limit: int,
) -> str:
    """Flux for `GET /telemetry`. Every value is checked or quoted; clients never send Flux."""
    if agg not in AGGREGATES:
        raise ValueError(f"agg must be one of: {', '.join(AGGREGATES)}")
    stop_arg = f", stop: {flux_time(stop)}" if stop else ""
    parts = [
        f"from(bucket: {flux_string(bucket)})",
        f"|> range(start: {flux_time(start)}{stop_arg})",
        f"|> filter(fn: (r) => r._measurement == {flux_string(MEASUREMENT)})",
    ]
    for column, value in (("device", device), ("sensor", sensor), ("_field", field)):
        if value is not None:
            parts.append(f"|> filter(fn: (r) => r.{column} == {flux_string(value)})")
    if every:
        parts.append(
            f"|> aggregateWindow(every: {flux_duration(every)}, fn: {agg}, createEmpty: false)"
        )
    parts += [
        '|> keep(columns: ["_time", "_value", "_field", "device", "sensor"])',
        "|> group()",
        '|> sort(columns: ["_time"])',
        f"|> limit(n: {int(limit)})",
    ]
    return "\n  ".join(parts)


INFERENCE_MEASUREMENT = "inference_result"
INFERENCE_FIELDS = ("confidence", "anomaly_score", "latency_ms")


def build_results_query(
    bucket: str,
    *,
    start: str,
    stop: str | None,
    device: str | None,
    label: str | None,
    mode: str | None,
    limit: int,
) -> str:
    """Flux for the edge runner's inference results (`ai_events`), one row per result with
    its fields side by side, newest first."""
    stop_arg = f", stop: {flux_time(stop)}" if stop else ""
    parts = [
        f"from(bucket: {flux_string(bucket)})",
        f"|> range(start: {flux_time(start)}{stop_arg})",
        f"|> filter(fn: (r) => r._measurement == {flux_string(INFERENCE_MEASUREMENT)})",
    ]
    for column, value in (("device", device), ("label", label), ("mode", mode)):
        if value is not None:
            parts.append(f"|> filter(fn: (r) => r.{column} == {flux_string(value)})")
    columns = ", ".join(
        flux_string(c) for c in ("_time", "device", "label", "mode", *INFERENCE_FIELDS)
    )
    parts += [
        '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")',
        f"|> keep(columns: [{columns}])",
        "|> group()",
        '|> sort(columns: ["_time"], desc: true)',
        f"|> limit(n: {int(limit)})",
    ]
    return "\n  ".join(parts)


def parse_csv(text: str) -> list[dict]:
    """Rows of InfluxDB's CSV response (with the `datatype` annotation), values typed."""
    rows: list[dict] = []
    types: list[str] = []
    header: list[str] | None = None
    for record in csv.reader(io.StringIO(text)):
        if not record or not any(record):
            header = None  # a blank line ends a table; the next one repeats the annotations
            continue
        if record[0] == "#datatype":
            types, header = record, None
            continue
        if record[0].startswith("#"):
            continue
        if header is None:
            header = record
            continue
        row = {}
        for name, kind, raw in zip(header, types or [""] * len(header), record, strict=False):
            if name in ("", "result", "table"):
                continue
            row[name] = _typed(kind, raw)
        rows.append(row)
    return rows


def _typed(kind: str, raw: str) -> object:
    if raw == "":
        return None
    if kind == "double":
        return float(raw)
    if kind in ("long", "unsignedLong"):
        return int(raw)
    if kind == "boolean":
        return raw == "true"
    return raw


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _client(cfg: Config) -> httpx.AsyncClient:
    if not cfg.token:
        raise NotConfigured(
            "InfluxDB isn't configured: set P4N4_API_INFLUXDB_TOKEN, or add the iot layer "
            "(its .env has INFLUXDB_TOKEN)."
        )
    return httpx.AsyncClient(
        base_url=cfg.url,
        headers={"Authorization": f"Token {cfg.token}"},
        timeout=TIMEOUT_S,
        transport=transport,
    )


def _error(response: httpx.Response) -> InfluxError:
    try:
        message = response.json().get("message") or response.text
    except ValueError:
        message = response.text
    return InfluxError(
        f"InfluxDB: {message.strip() or response.reason_phrase}", response.status_code
    )


async def write(cfg: Config, lines: list[str]) -> None:
    async with _client(cfg) as client:
        try:
            response = await client.post(
                "/api/v2/write",
                params={"org": cfg.org, "bucket": cfg.bucket, "precision": "ms"},
                content="\n".join(lines),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
        except httpx.HTTPError as exc:
            raise InfluxError(f"InfluxDB unreachable at {cfg.url}: {exc}") from exc
    if response.status_code != 204:
        raise _error(response)


async def query(cfg: Config, flux: str) -> list[dict]:
    async with _client(cfg) as client:
        try:
            response = await client.post(
                "/api/v2/query",
                params={"org": cfg.org},
                json={
                    "query": flux,
                    "type": "flux",
                    "dialect": {"header": True, "annotations": ["datatype"]},
                },
                headers={"Accept": "application/csv"},
            )
        except httpx.HTTPError as exc:
            raise InfluxError(f"InfluxDB unreachable at {cfg.url}: {exc}") from exc
    if response.status_code != 200:
        raise _error(response)
    return parse_csv(response.text)


async def ping(cfg: Config) -> str | None:
    """None when InfluxDB answers its health check, else why not (for /ready)."""
    try:
        async with httpx.AsyncClient(timeout=3, transport=transport) as client:
            response = await client.get(f"{cfg.url}/health")
    except httpx.HTTPError as exc:
        return f"unreachable at {cfg.url}: {exc}"
    return None if response.status_code == 200 else f"health check returned {response.status_code}"
