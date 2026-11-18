"""Base settings for every service. Values come from environment variables only."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "development", "staging", "production"]


class ServiceSettings(BaseSettings):
    """Settings common to all services.

    Secrets must be typed as ``SecretStr`` in subclasses so they never appear
    in reprs or logs.
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    service_name: str = "unknown-service"
    service_version: str = "0.1.0"
    environment: Environment = "local"
    log_level: str = "INFO"
    # Optional OpenTelemetry OTLP/HTTP endpoint, e.g. http://otel-lgtm:4318
    otel_exporter_otlp_endpoint: str | None = None
    health_check_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"
