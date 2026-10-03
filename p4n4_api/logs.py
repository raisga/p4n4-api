"""Request IDs and log formatting.

Every request gets an ID: the caller's `X-Request-ID` when it's sane (so a proxy's or the
dashboard's ID carries through), else a new one. It's returned in the response header and
added to every log line written while the request runs, and each request is logged once
on `p4n4_api.access`.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Request-ID"
_VALID_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
# Fields an access line carries besides the message, for the JSON format.
_ACCESS_FIELDS = ("method", "path", "status", "duration_ms", "client")

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
access_log = logging.getLogger("p4n4_api.access")


class RequestIdMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware), so streaming responses pass straight through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        incoming = next((v for k, v in scope["headers"] if k == b"x-request-id"), b"")
        incoming = incoming.decode("latin1")
        # Anything odd (too long, spaces, newlines) is replaced: it ends up in log lines.
        rid = incoming if _VALID_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        status = 500  # if the app raises before responding
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            ms = round((time.perf_counter() - start) * 1000, 1)
            client = scope["client"][0] if scope.get("client") else None
            fields = {
                "method": scope["method"],
                "path": scope["path"],
                "status": status,
                "duration_ms": ms,
                "client": client,
            }
            access_log.info("%s %s %s %.1fms client=%s", *fields.values(), extra=fields)
            request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every record ("-" outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        entry |= {k: getattr(record, k) for k in _ACCESS_FIELDS if hasattr(record, k)}
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def log_config(fmt: str, level: str) -> dict:
    """`logging.config.dictConfig` for `p4n4-api serve` (passed to uvicorn as `log_config`),
    covering uvicorn's loggers too so its lines get the same format and request ID."""
    formatter = (
        {"()": JsonFormatter}
        if fmt == "json"
        else {"format": "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"}
    )
    level = level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {"default": formatter},
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "filters": ["request_id"],
                "stream": "ext://sys.stderr",
            }
        },
        "loggers": {
            "p4n4_api": {"handlers": ["stderr"], "level": level, "propagate": False},
            "uvicorn": {"handlers": ["stderr"], "level": level, "propagate": False},
        },
    }
