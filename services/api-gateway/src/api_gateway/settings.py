from __future__ import annotations

import ipaddress
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import NoDecode

from api_gateway.edge import RateLimits
from api_gateway.ratelimit import Rule
from careerpilot_common.config import ServiceSettings

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


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

    # Token verification keys, fetched from Identity's internal JWKS endpoint.
    jwks_cache_ttl_seconds: float = Field(default=300, gt=0, le=3600)
    jwks_min_refresh_interval_seconds: float = Field(default=30, ge=0, le=600)

    # Rate limits (fixed windows, counted in Redis).
    rate_limit_login_per_ip: int = Field(default=20, ge=1)
    rate_limit_login_per_ip_window_seconds: int = Field(default=60, ge=1)
    # Failed logins per account (401s from Identity), whatever the source IP.
    rate_limit_login_failures_per_account: int = Field(default=5, ge=1)
    rate_limit_login_failures_window_seconds: int = Field(default=900, ge=1)
    rate_limit_register_per_ip: int = Field(default=10, ge=1)
    rate_limit_register_per_ip_window_seconds: int = Field(default=600, ge=1)
    rate_limit_refresh_per_ip: int = Field(default=60, ge=1)
    rate_limit_refresh_per_ip_window_seconds: int = Field(default=60, ge=1)

    # Proxies (CIDRs) whose X-Forwarded-For is trusted for the client address,
    # e.g. the TLS load balancer or the Web BFF. Empty = use the TCP peer.
    trusted_proxy_cidrs: Annotated[list[IpNetwork], NoDecode] = Field(default_factory=list)

    @field_validator("cors_allowed_origins", "trusted_proxy_cidrs", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
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

    def jwks_url(self) -> str:
        return self.identity_service_url.rstrip("/") + "/.well-known/jwks.json"

    def rate_limits(self) -> RateLimits:
        return RateLimits(
            login_per_ip=Rule("login-ip", self.rate_limit_login_per_ip, self.rate_limit_login_per_ip_window_seconds),
            login_failures_per_account=Rule(
                "login-fail",
                self.rate_limit_login_failures_per_account,
                self.rate_limit_login_failures_window_seconds,
            ),
            register_per_ip=Rule(
                "register-ip", self.rate_limit_register_per_ip, self.rate_limit_register_per_ip_window_seconds
            ),
            refresh_per_ip=Rule(
                "refresh-ip", self.rate_limit_refresh_per_ip, self.rate_limit_refresh_per_ip_window_seconds
            ),
        )

    def downstream_services(self) -> dict[str, str]:
        """Services whose readiness is reported by GET /v1/health."""
        return {"identity-service": self.identity_service_url}
