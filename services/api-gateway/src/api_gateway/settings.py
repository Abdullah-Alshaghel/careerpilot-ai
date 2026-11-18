from __future__ import annotations

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import NoDecode

from careerpilot_common.config import ServiceSettings


class GatewaySettings(ServiceSettings):
    service_name: str = "api-gateway"

    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    identity_service_url: str = "http://127.0.0.1:8101"
    upstream_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    # CV/document uploads are capped at 10 MB by the Document Service; allow
    # multipart overhead on top.
    max_request_body_bytes: int = Field(default=12 * 1024 * 1024, gt=0)

    # Explicit allow-list. Desktop (Tauri) origins:
    #   Windows: http://tauri.localhost   macOS: tauri://localhost
    #   Dev (Vite): http://localhost:1420
    # Web dev (Next.js): http://localhost:3000
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _no_wildcard(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("wildcard CORS origin is not allowed; list origins explicitly")
        return value

    def route_table(self) -> dict[str, str]:
        """Public path prefix -> upstream base URL. Longest prefix wins.

        Routes are added as each service exposes public endpoints (Phase 2+).
        """
        return {
            "/v1/auth": self.identity_service_url,
            "/v1/users": self.identity_service_url,
        }

    def downstream_services(self) -> dict[str, str]:
        """Services whose readiness is reported by GET /v1/health."""
        return {"identity-service": self.identity_service_url}
