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


def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # The client only learns that something failed. Starlette re-raises the exception
    # after this response, so the server still logs the traceback.
    return _response(500, ErrorDetail(code="internal_error", message="Internal server error."))


def install(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
