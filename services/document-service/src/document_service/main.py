from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from careerpilot_common.app import create_service_app
from careerpilot_common.auth import JwksCache, http_jwks_fetcher
from careerpilot_common.health import HealthCheck, postgres_check
from document_service.api import build_router
from document_service.settings import DocumentSettings
from document_service.storage import ObjectStore


def create_app(settings: DocumentSettings | None = None) -> FastAPI:
    """App factory (uvicorn runs it with ``--factory``)."""
    settings = settings or DocumentSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = app.state
        if not hasattr(state, "engine"):
            state.engine = create_async_engine(
                settings.database_url.get_secret_value(), pool_size=settings.db_pool_size, pool_pre_ping=True
            )
        state.sessionmaker = async_sessionmaker(state.engine, expire_on_commit=False)
        if not hasattr(state, "store"):
            state.store = ObjectStore(settings)
            await state.store.ensure_bucket()
        if not hasattr(state, "http"):
            state.http = httpx.AsyncClient(timeout=5, follow_redirects=False)
        if not hasattr(state, "jwks"):
            state.jwks = JwksCache(http_jwks_fetcher(state.http, settings.identity_jwks_url))
        try:
            yield
        finally:
            await state.http.aclose()
            await state.engine.dispose()

    def checks() -> Mapping[str, HealthCheck]:
        state = app.state
        if hasattr(state, "health_checks"):  # test override
            return state.health_checks
        return {"postgres": postgres_check(state.engine), "object-storage": state.store.check}

    app: FastAPI
    app = create_service_app(settings, checks=checks, routers=[build_router()], lifespan=lifespan)
    app.state.settings = settings
    return app
