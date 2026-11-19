from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api_gateway.edge import EdgePolicy
from api_gateway.proxy import build_proxy_router
from api_gateway.ratelimit import RateLimiter
from api_gateway.settings import GatewaySettings
from careerpilot_common.app import create_service_app
from careerpilot_common.auth import JwksCache
from careerpilot_common.health import (
    HealthCheck,
    HealthResponse,
    build_health_response,
    http_ready_check,
    redis_check,
    run_checks,
)


def _checks_for(app: FastAPI) -> Mapping[str, HealthCheck]:
    state = app.state
    settings: GatewaySettings = state.settings
    checks: dict[str, HealthCheck] = {"redis": redis_check(state.redis)}
    for name, base_url in settings.downstream_services().items():
        checks[name] = http_ready_check(state.http, base_url.rstrip("/") + "/health/ready")
    return checks


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    settings = settings or GatewaySettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Tests may pre-populate these with fakes.
        if not hasattr(app.state, "http"):
            app.state.http = httpx.AsyncClient(timeout=settings.upstream_timeout_seconds, follow_redirects=False)
        if not hasattr(app.state, "redis"):
            app.state.redis = redis.from_url(settings.redis_url.get_secret_value())
        if not hasattr(app.state, "jwks"):
            http: httpx.AsyncClient = app.state.http

            async def fetch_jwks() -> dict:
                resp = await http.get(settings.jwks_url())
                resp.raise_for_status()
                return resp.json()

            app.state.jwks = JwksCache(
                fetch_jwks,
                ttl=settings.jwks_cache_ttl_seconds,
                min_refresh_interval=settings.jwks_min_refresh_interval_seconds,
            )
        try:
            yield
        finally:
            await app.state.http.aclose()
            await app.state.redis.aclose()

    public = APIRouter(tags=["public"])

    @public.get("/v1/health", response_model=HealthResponse)
    async def public_health(request: Request) -> HealthResponse:
        """Platform health for clients. Always 200 while the gateway is up;
        the ``status`` field reports degradation."""
        results = await run_checks(_checks_for(request.app), settings.health_check_timeout_seconds)
        return build_health_response(settings.service_name, settings.service_version, results)

    app: FastAPI
    policy = EdgePolicy(
        limiter=lambda: RateLimiter(app.state.redis),
        jwks=lambda: app.state.jwks,
        limits=settings.rate_limits(),
        trusted_proxies=settings.trusted_proxy_cidrs,
    )
    app = create_service_app(
        settings,
        checks=lambda: _checks_for(app),
        routers=[public, build_proxy_router(settings.route_table(), settings.max_request_body_bytes, policy)],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        # Bearer tokens only; cookies are never sent cross-origin.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # Must cover every header @careerpilot/api-client sends (tested in tests/integration).
        allow_headers=["authorization", "content-type", "x-correlation-id", "x-client-type", "idempotency-key"],
        expose_headers=["x-correlation-id", "retry-after"],
        max_age=600,
    )
    return app


app = create_app()
