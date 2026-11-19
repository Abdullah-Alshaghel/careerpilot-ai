"""Edge policy applied to every proxied request before it reaches a service:
authentication (bearer access tokens) and rate limiting.

This is an early-reject layer. Services still verify tokens themselves and own
all authorization decisions (ADR-002, ADR-013).
"""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse

from api_gateway.ratelimit import Decision, RateLimiter, Rule
from careerpilot_common.auth import JwksCache, TokenError, bearer_token
from careerpilot_common.errors import error_body

log = logging.getLogger("api_gateway.edge")

# Reachable without an access token. Everything else under /v1 requires one.
PUBLIC_ROUTES = frozenset(
    {
        ("POST", "/v1/auth/register"),
        ("POST", "/v1/auth/login"),
        ("POST", "/v1/auth/refresh"),
        ("POST", "/v1/auth/logout"),
    }
)
# Set only by the gateway after verifying a token. Clients can't supply them.
AUTHENTICATED_USER_HEADER = "x-authenticated-user-id"
AUTHENTICATED_SESSION_HEADER = "x-authenticated-session-id"
GATEWAY_OWNED_HEADERS = frozenset({AUTHENTICATED_USER_HEADER, AUTHENTICATED_SESSION_HEADER, "x-user-id"})

_LOGIN_BODY_MAX = 8 * 1024

ReadBody = Callable[[], Awaitable[bytes | None]]


@dataclass
class Admission:
    reject: JSONResponse | None = None
    headers: list[tuple[str, str]] = field(default_factory=list)
    on_response: Callable[[int], Awaitable[None]] | None = None


@dataclass(frozen=True)
class RateLimits:
    login_per_ip: Rule
    login_failures_per_account: Rule
    register_per_ip: Rule
    refresh_per_ip: Rule


def client_ip(request: Request, trusted_proxies: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> str:
    """The caller's address. ``X-Forwarded-For`` is honoured only when the
    direct peer is a trusted proxy, reading right-to-left past trusted hops."""
    peer = request.client.host if request.client else "unknown"
    if not trusted_proxies:
        return peer

    def trusted(value: str) -> bool:
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            return False
        return any(addr in net for net in trusted_proxies)

    if not trusted(peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not trusted(hop):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer
    return peer


def _login_email(body: bytes | None) -> str | None:
    if not body or len(body) > _LOGIN_BODY_MAX:
        return None
    try:
        email = json.loads(body).get("email")
    except (ValueError, AttributeError):
        return None
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def _too_many(decision: Decision) -> JSONResponse:
    return JSONResponse(
        error_body("rate_limited", "Too many attempts. Try again later"),
        status_code=429,
        headers={"Retry-After": str(decision.retry_after)},
    )


def _unauthorized(code: str, message: str) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=401, headers={"WWW-Authenticate": "Bearer"})


class EdgePolicy:
    def __init__(
        self,
        *,
        limiter: Callable[[], RateLimiter],
        jwks: Callable[[], JwksCache],
        limits: RateLimits,
        trusted_proxies: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network] = (),
    ) -> None:
        # Resolved per request so tests and the lifespan can swap dependencies.
        self._limiter = limiter
        self._jwks = jwks
        self._limits = limits
        self._trusted_proxies = tuple(trusted_proxies)

    def client_ip(self, request: Request) -> str:
        return client_ip(request, self._trusted_proxies)

    async def admit(self, request: Request, path: str, read_body: ReadBody) -> Admission:
        route = (request.method, path)
        if route in PUBLIC_ROUTES:
            return await self._admit_public(request, path, read_body)
        return await self._authenticate(request)

    async def _admit_public(self, request: Request, path: str, read_body: ReadBody) -> Admission:
        limiter, ip = self._limiter(), self.client_ip(request)
        if path == "/v1/auth/login":
            decision = await limiter.hit(self._limits.login_per_ip, ip)
            if not decision.allowed:
                return Admission(reject=_too_many(decision))
            email = _login_email(await read_body())
            if email is None:
                return Admission()  # the service rejects the malformed body
            decision = await limiter.check(self._limits.login_failures_per_account, email)
            if not decision.allowed:
                log.warning("login blocked: too many failures for account")
                return Admission(reject=_too_many(decision))

            async def record(status: int) -> None:
                if status == 401:
                    await limiter.hit(self._limits.login_failures_per_account, email)

            return Admission(on_response=record)

        rule = self._limits.register_per_ip if path == "/v1/auth/register" else self._limits.refresh_per_ip
        decision = await limiter.hit(rule, ip)
        return Admission() if decision.allowed else Admission(reject=_too_many(decision))

    async def _authenticate(self, request: Request) -> Admission:
        token = bearer_token(request.headers.get("authorization"))
        if token is None:
            return Admission(reject=_unauthorized("unauthorized", "Sign in required"))
        try:
            claims = await self._jwks().verify(token)
        except TokenError as exc:
            return Admission(reject=_unauthorized(exc.code, "Sign in again"))
        except Exception as exc:
            # Key set unavailable (Identity down and nothing cached).
            log.error("cannot load token verification keys", extra={"error_type": type(exc).__name__})
            return Admission(
                reject=JSONResponse(
                    error_body("auth_unavailable", "Sign-in is temporarily unavailable"), status_code=503
                )
            )
        return Admission(
            headers=[
                (AUTHENTICATED_USER_HEADER, str(claims.user_id)),
                (AUTHENTICATED_SESSION_HEADER, str(claims.session_id)),
            ]
        )
