"""Prefix-based reverse proxy. Routing and protection only (architecture §4.2):
authorization and business rules are enforced by the owning service."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from api_gateway.edge import GATEWAY_OWNED_HEADERS, EdgePolicy
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
    *GATEWAY_OWNED_HEADERS,
}
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def resolve_upstream(path: str, routes: dict[str, str]) -> str | None:
    best: str | None = None
    for prefix in routes:
        if (path == prefix or path.startswith(prefix + "/")) and (best is None or len(prefix) > len(best)):
            best = prefix
    return routes[best] if best else None


def _forward_headers(request: Request, client_ip: str, extra: list[tuple[str, str]]) -> list[tuple[str, str]]:
    # Identity headers from the client are dropped (in _HOP_BY_HOP); only the
    # edge policy's verified values in ``extra`` are forwarded.
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP]
    headers.append((CORRELATION_HEADER, get_correlation_id() or ""))
    headers.append(("x-forwarded-for", client_ip))
    headers.append(("x-forwarded-proto", request.url.scheme))
    headers.extend(extra)
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


class _BodyReader:
    """Reads the request body at most once (the edge policy may need it first)."""

    def __init__(self, request: Request, limit: int) -> None:
        self._request = request
        self._limit = limit
        self._read = False
        self._body: bytes | None = None

    async def __call__(self) -> bytes | None:
        if not self._read:
            self._body = await _read_limited_body(self._request, self._limit)
            self._read = True
        return self._body


def build_proxy_router(
    routes: dict[str, str],
    max_body_bytes: int,
    policy: EdgePolicy,
    timeouts: dict[tuple[str, str], float] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.api_route("/v1/{path:path}", methods=_METHODS, include_in_schema=False)
    async def proxy(request: Request, path: str):
        full_path = f"/v1/{path}"
        upstream = resolve_upstream(full_path, routes)
        if upstream is None:
            return JSONResponse(error_body("not_found", "Route not found"), status_code=404)

        read_body = _BodyReader(request, max_body_bytes)
        # Authentication runs before the body is buffered, so unauthenticated
        # callers can't make the gateway hold large uploads in memory.
        admission = await policy.admit(request, full_path, read_body)
        if admission.reject is not None:
            return admission.reject
        body = await read_body()
        if body is None:
            return JSONResponse(error_body("payload_too_large", "Request body too large"), status_code=413)

        client: httpx.AsyncClient = request.app.state.http
        upstream_request = client.build_request(
            request.method,
            upstream.rstrip("/") + full_path,
            params=request.query_params.multi_items(),
            headers=_forward_headers(request, policy.client_ip(request), admission.headers),
            content=body,
            **(
                {"timeout": timeouts[(request.method, full_path)]}
                if timeouts and (request.method, full_path) in timeouts
                else {}
            ),
        )
        try:
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.TimeoutException:
            log.warning("upstream timeout", extra={"upstream_path": full_path})
            return JSONResponse(error_body("upstream_timeout", "Upstream service timed out"), status_code=504)
        except httpx.TransportError as exc:
            log.warning("upstream unavailable", extra={"upstream_path": full_path, "error_type": type(exc).__name__})
            return JSONResponse(error_body("upstream_unavailable", "Upstream service unavailable"), status_code=502)

        if admission.on_response is not None:
            await admission.on_response(upstream_response.status_code)
        response_headers = {k: v for k, v in upstream_response.headers.items() if k.lower() not in _HOP_BY_HOP}
        return StreamingResponse(
            upstream_response.aiter_raw(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream_response.aclose),
        )

    return router
