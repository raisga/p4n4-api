"""One error body for every failure: `{"error": {"code": ..., "message": ...}}`.

`code` is a stable, machine-readable slug (from the status, or set with `ApiError` where the
status alone is ambiguous); `message` is for people and may change.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    504: "upstream_timeout",
    503: "unavailable",
}


class FieldError(BaseModel):
    loc: list[str | int]
    message: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    # Only for validation_error: which fields were wrong and why
    fields: list[FieldError] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# For the OpenAPI spec: every 4xx/5xx carries an ErrorResponse.
RESPONSES = {
    "4XX": {"model": ErrorResponse, "description": "Client error"},
    "5XX": {"model": ErrorResponse, "description": "Server or upstream error"},
}


class ApiError(HTTPException):
    """An HTTPException with a specific `code`, for when the status alone is ambiguous."""

    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code


def _response(status: int, error: ErrorDetail, headers: dict | None = None) -> JSONResponse:
    body = ErrorResponse(error=error).model_dump(exclude_none=True)
    return JSONResponse(status_code=status, content=body, headers=headers)


def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = getattr(exc, "code", None) or _CODES.get(exc.status_code, f"http_{exc.status_code}")
    return _response(exc.status_code, ErrorDetail(code=code, message=str(exc.detail)), exc.headers)


def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [FieldError(loc=list(e["loc"]), message=e["msg"]) for e in exc.errors()]
    first = fields[0] if fields else None
    message = (
        f"Invalid request: {'.'.join(map(str, first.loc))}: {first.message}"
        if first
        else "Invalid request."
    )
    return _response(422, ErrorDetail(code="validation_error", message=message, fields=fields))


def _unhandled(request: Request | None, exc: Exception) -> JSONResponse:
    # The client only learns that something failed. The exception is re-raised after this
    # response, so the server still logs the traceback.
    return _response(500, ErrorDetail(code="internal_error", message="Internal server error."))


class UnhandledErrorMiddleware:
    """Answers unhandled exceptions with the standard 500 body from inside our middleware.

    Starlette's own handler for `Exception` runs in `ServerErrorMiddleware`, outside every
    middleware we add, so its 500 had no CORS headers (a cross-origin browser saw a CORS
    error instead of the body) and no `X-Request-ID`. Added first, this runs innermost of
    ours, and its response goes back out through them. Pure ASGI, so streams pass through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        started = False

        async def send_tracked(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracked)
        except Exception as exc:
            # Mid-stream there's nothing left to answer with; the server drops the connection.
            if not started:
                await _unhandled(None, exc)(scope, receive, send)
            # ServerErrorMiddleware sees the response started and only re-raises, so the
            # server logs the traceback and test clients can still raise it.
            raise


def install(app: FastAPI) -> None:
    """Exception handlers, and the 500 middleware: call before adding other middleware."""
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    # Still used for failures in our outer middleware, which the class above can't see.
    app.add_exception_handler(Exception, _unhandled)
    app.add_middleware(UnhandledErrorMiddleware)
