from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from careerpilot_common.app import create_service_app
from careerpilot_common.health import HealthCheck, postgres_check, rabbitmq_check
from identity_service.settings import IdentitySettings


def create_app(settings: IdentitySettings | None = None) -> FastAPI:
    settings = settings or IdentitySettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "engine"):
            app.state.engine = create_async_engine(
                settings.database_url.get_secret_value(),
                pool_size=settings.db_pool_size,
                pool_pre_ping=True,
            )
        try:
            yield
        finally:
            await app.state.engine.dispose()

    def checks() -> Mapping[str, HealthCheck]:
        state = app.state
        if hasattr(state, "health_checks"):  # test override
            return state.health_checks
        return {
            "postgres": postgres_check(state.engine),
            "rabbitmq": rabbitmq_check(settings.rabbitmq_url.get_secret_value(), settings.health_check_timeout_seconds),
        }

    app: FastAPI
    app = create_service_app(settings, checks=checks, lifespan=lifespan)
    return app


app = create_app()
