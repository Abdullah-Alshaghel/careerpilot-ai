"""Correlation ID propagation (architecture doc §12, §16).

Every request carries an ``X-Correlation-ID``. Incoming values are accepted
only if they match a strict format; otherwise a new UUID is generated, which
prevents log injection through the header.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = "x-correlation-id"
CLIENT_TYPE_HEADER = "x-client-type"
_CLIENT_TYPES = frozenset({"desktop", "web"})
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_access_log = logging.getLogger("careerpilot.access")
_error_log = logging.getLogger("careerpilot.errors")


def get_correlation_id() -> str | None:
    return correlation_id_var.get()


def normalize_correlation_id(value: str | None) -> str:
    if value and _VALID_ID.match(value):
        return value
    return str(uuid.uuid4())


class CorrelationIdMiddleware:
    """Pure ASGI middleware: sets the correlation context and logs each request.

    Only method, route path, status and duration are logged — never query
    strings, headers or bodies, which may contain tokens or CV contents.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        client_type = None
        for name, value in scope.get("headers", []):
            if name == CORRELATION_HEADER.encode():
                incoming = value.decode("latin-1")
            elif name == CLIENT_TYPE_HEADER.encode():
                decoded = value.decode("latin-1")
                # Allow-listed values only: header content is never logged verbatim.
                client_type = decoded if decoded in _CLIENT_TYPES else "other"
        cid = normalize_correlation_id(incoming)
        token = correlation_id_var.set(cid)
        started = time.perf_counter()
        state = {"status": 500, "started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["started"] = True
                headers = list(message.get("headers", []))
                headers.append((CORRELATION_HEADER.encode(), cid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Handled here (not in a FastAPI exception handler) so the response
            # still carries the correlation ID. Details are logged, not returned.
            _error_log.error("unhandled exception", exc_info=True)
            if state["started"]:
                raise
            body = json.dumps(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "An unexpected error occurred",
                        "correlation_id": cid,
                    }
                }
            ).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            _access_log.info(
                "request completed",
                extra={
                    "http_method": scope.get("method"),
                    "http_path": scope.get("path"),
                    "http_status": state["status"],
                    "client_type": client_type,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            correlation_id_var.reset(token)
