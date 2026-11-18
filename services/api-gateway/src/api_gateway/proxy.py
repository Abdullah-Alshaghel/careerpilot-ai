"""Prefix-based reverse proxy. Routing and protection only (architecture §4.2):
authorization and business rules are enforced by the owning service."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from careerpilot_common.correlation import CORRELATION_HEADER, get_correlation_id
from careerpilot_common.errors import error_body

log = logging.getLogger("api_gateway.proxy")

# RFC 7230 hop-by-hop headers plus headers the gateway owns.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    CORRELATION_HEADER,
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
}
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def resolve_upstream(path: str, routes: dict[str, str]) -> str | None:
    best: str | None = None
    for prefix in routes:
        if (path == prefix or path.startswith(prefix + "/")) and (best is None or len(prefix) > len(best)):
            best = prefix
    return routes[best] if best else None


def _forward_headers(request: Request) -> list[tuple[str, str]]:
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP]
    headers.append((CORRELATION_HEADER, get_correlation_id() or ""))
    if request.client:
        headers.append(("x-forwarded-for", request.client.host))
    headers.append(("x-forwarded-proto", request.url.scheme))
    return headers


async def _read_limited_body(request: Request, limit: int) -> bytes | None:
    """Buffer the body, refusing more than ``limit`` bytes (None = too large).

    Buffering (rather than streaming) bounds memory per request and lets the
    gateway reject oversized uploads before they reach a service.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def build_proxy_router(routes: dict[str, str], max_body_bytes: int) -> APIRouter:
    router = APIRouter()

    @router.api_route("/v1/{path:path}", methods=_METHODS, include_in_schema=False)
    async def proxy(request: Request, path: str):
        full_path = f"/v1/{path}"
        upstream = resolve_upstream(full_path, routes)
        if upstream is None:
            return JSONResponse(error_body("not_found", "Route not found"), status_code=404)

        body = await _read_limited_body(request, max_body_bytes)
        if body is None:
            return JSONResponse(error_body("payload_too_large", "Request body too large"), status_code=413)

        client: httpx.AsyncClient = request.app.state.http
        upstream_request = client.build_request(
            request.method,
            upstream.rstrip("/") + full_path,
            params=request.query_params.multi_items(),
            headers=_forward_headers(request),
            content=body,
        )
        try:
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.TimeoutException:
            log.warning("upstream timeout", extra={"upstream_path": full_path})
            return JSONResponse(error_body("upstream_timeout", "Upstream service timed out"), status_code=504)
        except httpx.TransportError as exc:
            log.warning("upstream unavailable", extra={"upstream_path": full_path, "error_type": type(exc).__name__})
            return JSONResponse(error_body("upstream_unavailable", "Upstream service unavailable"), status_code=502)

        response_headers = {k: v for k, v in upstream_response.headers.items() if k.lower() not in _HOP_BY_HOP}
        return StreamingResponse(
            upstream_response.aiter_raw(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream_response.aclose),
        )

    return router
