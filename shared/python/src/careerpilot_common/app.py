"""FastAPI application factory used by every service."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager

from fastapi import APIRouter, FastAPI

from careerpilot_common.config import ServiceSettings
from careerpilot_common.correlation import CorrelationIdMiddleware
from careerpilot_common.errors import install_error_handlers
from careerpilot_common.health import HealthCheck, health_router
from careerpilot_common.logging import configure_logging

log = logging.getLogger("careerpilot.app")

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_service_app(
    settings: ServiceSettings,
    *,
    checks: Callable[[], Mapping[str, HealthCheck]] = dict,
    routers: Sequence[APIRouter] = (),
    lifespan: Lifespan | None = None,
) -> FastAPI:
    configure_logging(settings.service_name, settings.environment, settings.log_level)

    docs_enabled = not settings.is_production
    app = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    install_error_handlers(app)
    app.include_router(
        health_router(
            settings.service_name,
            settings.service_version,
            checks,
            settings.health_check_timeout_seconds,
        )
    )
    for router in routers:
        app.include_router(router)

    app.add_middleware(CorrelationIdMiddleware)
    _maybe_enable_telemetry(app, settings)
    return app


def _maybe_enable_telemetry(app: FastAPI, settings: ServiceSettings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL endpoint configured but telemetry extras are not installed")
        return

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": settings.service_version,
                "deployment.environment": settings.environment,
            }
        )
    )
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready")
    log.info("opentelemetry tracing enabled")
