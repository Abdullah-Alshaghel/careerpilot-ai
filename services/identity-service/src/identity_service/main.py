from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from careerpilot_common.app import create_service_app
from careerpilot_common.health import HealthCheck, RabbitMQProbe, postgres_check
from identity_service.api import build_router
from identity_service.security import PasswordHasher, TokenSigner, decode_signing_key
from identity_service.service import AuthContext
from identity_service.settings import IdentitySettings


def create_app(settings: IdentitySettings | None = None) -> FastAPI:
    """App factory (uvicorn runs it with ``--factory``)."""
    settings = settings or IdentitySettings()
    if settings.jwt_signing_key is None:
        raise RuntimeError("JWT_SIGNING_KEY is not set; generate one with `python scripts/init-env.py`")
    # Validated at startup so a bad key fails the deploy, not the first login.
    signer = TokenSigner(
        decode_signing_key(settings.jwt_signing_key.get_secret_value()),
        timedelta(seconds=settings.access_token_ttl_seconds),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "engine"):
            app.state.engine = create_async_engine(
                settings.database_url.get_secret_value(),
                pool_size=settings.db_pool_size,
                pool_pre_ping=True,
            )
        app.state.sessionmaker = async_sessionmaker(app.state.engine, expire_on_commit=False)
        app.state.rabbitmq_probe = RabbitMQProbe(
            settings.rabbitmq_url.get_secret_value(), settings.health_check_timeout_seconds
        )
        # Built here so its semaphore belongs to the running event loop.
        app.state.auth = AuthContext(
            hasher=PasswordHasher(settings.password_hash_concurrency),
            signer=signer,
            refresh_token_ttl=timedelta(seconds=settings.refresh_token_ttl_seconds),
            reuse_grace=timedelta(seconds=settings.refresh_reuse_grace_seconds),
        )
        try:
            yield
        finally:
            await app.state.rabbitmq_probe.close()
            await app.state.engine.dispose()

    def checks() -> Mapping[str, HealthCheck]:
        state = app.state
        if hasattr(state, "health_checks"):  # test override
            return state.health_checks
        return {
            "postgres": postgres_check(state.engine),
            "rabbitmq": state.rabbitmq_probe.check,
        }

    app: FastAPI
    app = create_service_app(settings, checks=checks, routers=[build_router()], lifespan=lifespan)
    return app
