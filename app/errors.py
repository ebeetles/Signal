"""One JSON error shape for every error response:

    {"error": {"code": "not_found", "message": "Digest 9 not found"}}

Validation errors add "details": [{"field": "limit", "message": "..."}].
Internal errors never include exception text.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None, headers: dict | None = None):
        self.status = status
        self.code = code or _CODES.get(status, "error")
        self.message = message
        self.headers = headers


def error_response(status: int, message: str, code: str | None = None, details=None, headers=None) -> JSONResponse:
    body: dict = {"error": {"code": code or _CODES.get(status, "error"), "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.message, exc.code, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        if exc.status_code == 404 and message == "Not Found":
            message = "No such endpoint"
        return error_response(exc.status_code, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        details = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", ()) if p not in ("query", "path", "body")]
            details.append({"field": ".".join(loc) or None, "message": err.get("msg", "invalid")})
        return error_response(422, "Invalid request parameters", details=details)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error: %s", type(exc).__name__)
        return error_response(500, "Internal server error")
