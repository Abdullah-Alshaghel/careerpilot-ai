"""Fixed-window rate limiting in Redis.

Fails open: if Redis is unavailable, requests are allowed and a warning is
logged (ADR-012). Argon2id and the per-account limits still slow brute-force
attempts, and an auth outage would otherwise follow every cache outage.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

log = logging.getLogger("api_gateway.ratelimit")


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: int = 0


ALLOW = Decision(True)


def _key(rule: Rule, identifier: str) -> str:
    # Identifiers (IP addresses, emails) are hashed: no personal data in Redis keys.
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
    return f"rl:{rule.name}:{digest}"


class RateLimiter:
    def __init__(self, redis: object) -> None:
        self._redis = redis

    async def hit(self, rule: Rule, identifier: str) -> Decision:
        """Count one event; deny once the window holds more than ``limit``."""
        key = _key(rule, identifier)
        try:
            pipe = self._redis.pipeline(transaction=True)  # type: ignore[attr-defined]
            pipe.incr(key)
            pipe.expire(key, rule.window_seconds, nx=True)
            pipe.ttl(key)
            count, _, ttl = await pipe.execute()
        except Exception as exc:
            log.warning("rate limiter unavailable; allowing request", extra={"error_type": type(exc).__name__})
            return ALLOW
        if int(count) <= rule.limit:
            return ALLOW
        return Decision(False, int(ttl) if int(ttl) > 0 else rule.window_seconds)

    async def check(self, rule: Rule, identifier: str) -> Decision:
        """Whether ``identifier`` is already over the limit, without counting."""
        key = _key(rule, identifier)
        try:
            pipe = self._redis.pipeline(transaction=False)  # type: ignore[attr-defined]
            pipe.get(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()
        except Exception as exc:
            log.warning("rate limiter unavailable; allowing request", extra={"error_type": type(exc).__name__})
            return ALLOW
        if count is None or int(count) < rule.limit:
            return ALLOW
        return Decision(False, int(ttl) if int(ttl) > 0 else rule.window_seconds)
