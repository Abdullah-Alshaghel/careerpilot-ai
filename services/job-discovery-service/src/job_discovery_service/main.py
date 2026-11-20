from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from careerpilot_common.app import create_service_app
from careerpilot_common.auth import JwksCache, http_jwks_fetcher
from careerpilot_common.health import HealthCheck, RabbitMQProbe, postgres_check
from careerpilot_common.messaging import EventPublisher
from job_discovery_service.api import build_router
from job_discovery_service.settings import DiscoverySettings
from job_discovery_service.worker import make_fetcher, run_loop


def create_app(settings: DiscoverySettings | None = None) -> FastAPI:
    """App factory (uvicorn runs it with ``--factory``)."""
    settings = settings or DiscoverySettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = app.state
        if not hasattr(state, "engine"):
            state.engine = create_async_engine(
                settings.database_url.get_secret_value(), pool_size=settings.db_pool_size, pool_pre_ping=True
            )
        state.sessionmaker = async_sessionmaker(state.engine, expire_on_commit=False)
        if not hasattr(state, "http"):  # internal calls (JWKS, Company Service)
            state.http = httpx.AsyncClient(timeout=10, follow_redirects=False)
        if not hasattr(state, "jwks"):
            state.jwks = JwksCache(http_jwks_fetcher(state.http, settings.identity_jwks_url))
        if not hasattr(state, "fetcher"):  # the only client that reaches the internet
            state.fetcher = make_fetcher(settings)
        state.wake_worker = asyncio.Event()
        state.rabbitmq_probe = RabbitMQProbe(
            settings.rabbitmq_url.get_secret_value(), settings.health_check_timeout_seconds
        )
        stop = asyncio.Event()
        worker = None
        publisher = None
        if settings.run_worker:
            publisher = EventPublisher(settings.rabbitmq_url.get_secret_value())
            worker = asyncio.create_task(
                run_loop(settings, state.sessionmaker, state.fetcher, publisher, stop, state.wake_worker)
            )
        try:
            yield
        finally:
            stop.set()
            if worker is not None:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker
            if publisher is not None:
                await publisher.close()
            await state.rabbitmq_probe.close()
            await state.fetcher.aclose()
            await state.http.aclose()
            await state.engine.dispose()

    def checks() -> Mapping[str, HealthCheck]:
        state = app.state
        if hasattr(state, "health_checks"):  # test override
            return state.health_checks
        return {"postgres": postgres_check(state.engine), "rabbitmq": state.rabbitmq_probe.check}

    app: FastAPI
    app = create_service_app(settings, checks=checks, routers=[build_router()], lifespan=lifespan)
    app.state.settings = settings
    return app
