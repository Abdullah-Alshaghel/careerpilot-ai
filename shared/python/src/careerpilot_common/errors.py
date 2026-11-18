"""Uniform, safe error responses.

Shape: ``{"error": {"code": str, "message": str, "correlation_id": str | null}}``
Internal exception details are logged (redacted) but never returned to clients.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from careerpilot_common.correlation import get_correlation_id


class ServiceError(Exception):
    """Raise for expected, client-visible failures."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "correlation_id": get_correlation_id()}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized", 403: "forbidden"}.get(
            exc.status_code, "http_error"
        )
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(error_body(code, message), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only — never echo submitted values back.
        fields = [{"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "")} for e in exc.errors()]
        body = error_body("validation_error", "Request validation failed")
        body["error"]["fields"] = fields  # type: ignore[index]
        return JSONResponse(body, status_code=422)

    # Unhandled exceptions are converted to 500s by CorrelationIdMiddleware so
    # the response keeps its correlation ID (see correlation.py).
