"""One error shape for every failure: {"error": {"code", "message", "details"?, "request_id"?}}."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("api")


class ApiError(Exception):
    """Raised by services for expected, client-visible failures."""

    def __init__(self, status: int, code: str, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def error_body(request: Request, code: str, message: str, details: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if details:
        err["details"] = details
    rid = getattr(request.state, "request_id", None)
    if rid:
        err["request_id"] = rid
    return {"error": err}


def _loc(loc) -> str:
    # ('body', 'events', 3, 'x') -> 'events[3].x'
    out = ""
    for part in loc:
        if part in ("body", "query", "path", "header"):
            continue
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "body"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=error_body(request, exc.code, exc.message, exc.details))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict):
            code, msg = exc.detail.get("code", "HTTP_ERROR"), exc.detail.get("message", "")
        else:
            code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
            msg = str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content=error_body(request, code, msg),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        if any(e.get("type") == "json_invalid" for e in errors):
            return JSONResponse(status_code=400, content=error_body(
                request, "INVALID_JSON", "request body is not valid JSON"))
        details = [{"field": _loc(e.get("loc", ())), "message": e.get("msg", ""), "type": e.get("type")}
                   for e in errors]
        return JSONResponse(status_code=422, content=error_body(
            request, "VALIDATION_ERROR", f"{len(details)} validation error(s)", details))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content=error_body(request, "INTERNAL_ERROR", "unexpected server error"))
