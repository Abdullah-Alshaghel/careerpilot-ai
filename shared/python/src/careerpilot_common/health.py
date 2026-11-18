"""Health contract and dependency checks.

- ``GET /health/live``  — process is alive. Never touches dependencies.
- ``GET /health/ready`` — all dependencies reachable; 503 otherwise.

The response model mirrors ``shared/api-contracts/schemas/health.v1.schema.json``
and is verified by contract tests on both the Python and TypeScript sides.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field

HealthCheck = Callable[[], Awaitable[None]]
"""A check raises any exception on failure and returns ``None`` on success."""

CheckStatus = Literal["up", "down"]
OverallStatus = Literal["ok", "degraded", "down"]


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9-]+$", max_length=64)
    status: CheckStatus
    latency_ms: float = Field(ge=0)
    # Short, non-sensitive failure reason (exception type), never raw errors.
    detail: str | None = Field(default=None, max_length=200)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str = Field(max_length=64)
    version: str = Field(max_length=32)
    status: OverallStatus
    timestamp: datetime
    checks: list[CheckResult]


def overall_status(checks: list[CheckResult]) -> OverallStatus:
    if not checks or all(c.status == "up" for c in checks):
        return "ok"
    if all(c.status == "down" for c in checks):
        return "down"
    return "degraded"


async def run_check(name: str, check: HealthCheck, timeout: float) -> CheckResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=timeout)
    except asyncio.TimeoutError:
        result_status, detail = "down", "timeout"
    except Exception as exc:
        result_status, detail = "down", type(exc).__name__
    else:
        result_status, detail = "up", None
    latency = round((time.perf_counter() - started) * 1000, 2)
    return CheckResult(name=name, status=result_status, latency_ms=latency, detail=detail)


async def run_checks(checks: Mapping[str, HealthCheck], timeout: float) -> list[CheckResult]:
    return list(await asyncio.gather(*(run_check(n, c, timeout) for n, c in checks.items())))


def build_health_response(service: str, version: str, results: list[CheckResult]) -> HealthResponse:
    return HealthResponse(
        service=service,
        version=version,
        status=overall_status(results),
        timestamp=datetime.now(timezone.utc),
        checks=results,
    )


def health_router(
    service: str,
    version: str,
    checks: Callable[[], Mapping[str, HealthCheck]],
    timeout: float,
) -> APIRouter:
    """Liveness + readiness endpoints. ``checks`` is resolved per request so
    tests and lifespans can swap dependencies."""

    router = APIRouter(tags=["health"])

    @router.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return build_health_response(service, version, [])

    @router.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse}},
    )
    async def ready(response: Response) -> HealthResponse:
        body = build_health_response(service, version, await run_checks(checks(), timeout))
        if body.status != "ok":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return body

    return router


# --- Dependency check factories -------------------------------------------------


def postgres_check(engine: object) -> HealthCheck:
    """``engine`` is a SQLAlchemy AsyncEngine (typed loosely to keep the
    dependency optional)."""

    async def check() -> None:
        from sqlalchemy import text

        async with engine.connect() as conn:  # type: ignore[attr-defined]
            await conn.execute(text("SELECT 1"))

    return check


def redis_check(client: object) -> HealthCheck:
    async def check() -> None:
        await client.ping()  # type: ignore[attr-defined]

    return check


def rabbitmq_check(url: str, timeout: float) -> HealthCheck:
    async def check() -> None:
        import aio_pika

        connection = await aio_pika.connect(url, timeout=timeout)
        await connection.close()

    return check


def http_ready_check(client: object, url: str) -> HealthCheck:
    """Downstream service readiness via its ``/health/ready`` endpoint."""

    async def check() -> None:
        resp = await client.get(url)  # type: ignore[attr-defined]
        resp.raise_for_status()

    return check
