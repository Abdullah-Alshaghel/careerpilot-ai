"""Access-token verification shared by the gateway and every service.

Tokens are issued only by the Identity Service (EdDSA / Ed25519, RFC 9068
``at+jwt``). Verifiers only ever hold *public* keys, published by Identity as a
JWKS document. Each service verifies the bearer token itself (zero trust: the
internal network is not an authentication boundary); the gateway verifies too,
so invalid traffic is rejected at the edge.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import Request

from careerpilot_common.errors import ServiceError

ISSUER = "careerpilot-identity"
AUDIENCE = "careerpilot-api"
ACCESS_TOKEN_TYPE = "at+jwt"  # noqa: S105 - a JWT "typ" value, not a secret
ALGORITHM = "EdDSA"
# Tokens are small (~400 bytes); anything far larger is rejected before parsing.
MAX_TOKEN_LENGTH = 4096
DEFAULT_LEEWAY_SECONDS = 30
_REQUIRED_CLAIMS = ["iss", "aud", "sub", "sid", "iat", "exp", "jti"]


class TokenError(Exception):
    """Token rejected. ``code`` is safe to return to clients."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UnknownKeyError(TokenError):
    """Signed with a key id we don't know: the key set may need a refresh."""

    def __init__(self) -> None:
        super().__init__("invalid_token")


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def public_jwk(public_key: Ed25519PublicKey) -> dict[str, str]:
    """RFC 8037 OKP JWK with an RFC 7638 thumbprint as ``kid``."""
    x = _b64url(public_key.public_bytes(Encoding.Raw, PublicFormat.Raw))
    # RFC 7638: required members only, lexicographic order, no whitespace.
    canonical = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"), sort_keys=True)
    kid = _b64url(hashlib.sha256(canonical.encode()).digest())
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": kid, "alg": ALGORITHM, "use": "sig"}


class KeySet:
    """Public verification keys indexed by ``kid``."""

    def __init__(self, keys: Mapping[str, Ed25519PublicKey]) -> None:
        self._keys = dict(keys)

    def get(self, kid: str | None) -> Ed25519PublicKey | None:
        return self._keys.get(kid) if kid else None

    def __len__(self) -> int:
        return len(self._keys)

    @classmethod
    def from_public_keys(cls, *keys: Ed25519PublicKey) -> KeySet:
        return cls({public_jwk(k)["kid"]: k for k in keys})

    @classmethod
    def from_jwks(cls, document: Mapping[str, Any]) -> KeySet:
        """Accept only Ed25519 signing keys; anything else is ignored."""
        keys: dict[str, Ed25519PublicKey] = {}
        for jwk in document.get("keys", []):
            if not isinstance(jwk, dict):
                continue
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or jwk.get("use", "sig") != "sig":
                continue
            kid, x = jwk.get("kid"), jwk.get("x")
            if not isinstance(kid, str) or not isinstance(x, str):
                continue
            try:
                keys[kid] = Ed25519PublicKey.from_public_bytes(_b64url_decode(x))
            except ValueError:
                continue
        return cls(keys)


def verify_access_token(token: str, keys: KeySet, *, leeway: int = DEFAULT_LEEWAY_SECONDS) -> AccessTokenClaims:
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise TokenError("invalid_token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError("invalid_token") from exc
    # Pin the algorithm and token type: never trust the header to pick them.
    if header.get("alg") != ALGORITHM or header.get("typ") != ACCESS_TOKEN_TYPE:
        raise TokenError("invalid_token")
    key = keys.get(header.get("kid"))
    if key is None:
        raise UnknownKeyError()
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=leeway,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token_expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid_token") from exc
    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(str(claims["sub"])),
            session_id=uuid.UUID(str(claims["sid"])),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc),
        )
    except (ValueError, TypeError) as exc:
        raise TokenError("invalid_token") from exc


def bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class JwksCache:
    """Caches the Identity Service key set.

    Refreshes after ``ttl`` seconds, or on demand when a token names an unknown
    ``kid`` (key rotation), but at most once per ``min_refresh_interval`` so
    forged ``kid`` values cannot make us hammer Identity.
    """

    def __init__(
        self,
        fetch: Callable[[], Awaitable[Mapping[str, Any]]],
        *,
        ttl: float = 300.0,
        min_refresh_interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl
        self._min_refresh_interval = min_refresh_interval
        self._clock = clock
        self._keys: KeySet | None = None
        self._fetched_at = float("-inf")
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        async with self._lock:
            if self._clock() - self._fetched_at < self._min_refresh_interval and self._keys is not None:
                return  # another coroutine refreshed while we waited
            document = await self._fetch()
            self._keys = KeySet.from_jwks(document)
            self._fetched_at = self._clock()

    async def keys(self, *, force: bool = False) -> KeySet:
        """Raise whatever ``fetch`` raises if no key set could ever be loaded."""
        age = self._clock() - self._fetched_at
        stale = self._keys is None or age >= self._ttl
        if stale or (force and age >= self._min_refresh_interval):
            try:
                await self._refresh()
            except Exception:
                if self._keys is None:
                    raise
                # Identity briefly unavailable: keep verifying with known keys.
        assert self._keys is not None  # noqa: S101 - set by _refresh or raised above
        return self._keys

    async def verify(self, token: str) -> AccessTokenClaims:
        try:
            return verify_access_token(token, await self.keys())
        except UnknownKeyError:
            return verify_access_token(token, await self.keys(force=True))


def http_jwks_fetcher(http: Any, url: str) -> Callable[[], Awaitable[Mapping[str, Any]]]:
    """Fetch Identity's JWKS with an ``httpx.AsyncClient``."""

    async def fetch() -> Mapping[str, Any]:
        resp = await http.get(url)
        resp.raise_for_status()
        return resp.json()

    return fetch


def require_user(get_cache: Callable[[Request], JwksCache]) -> Callable[[Request], Awaitable[AccessTokenClaims]]:
    """FastAPI dependency: the verified caller, or a 401/503 error.

    Every domain service uses this on every user-facing route, then scopes all
    data access by ``claims.user_id`` (never by anything the client sends).
    ``get_cache`` receives the request and returns the service's JwksCache.
    """

    challenge = {"WWW-Authenticate": "Bearer"}

    async def dependency(request: Request) -> AccessTokenClaims:
        token = bearer_token(request.headers.get("authorization"))
        if token is None:
            raise ServiceError(401, "unauthorized", "Sign in required", headers=challenge)
        try:
            return await get_cache(request).verify(token)
        except TokenError as exc:
            raise ServiceError(401, exc.code, "Sign in again", headers=challenge) from exc
        except Exception as exc:
            raise ServiceError(503, "auth_unavailable", "Sign-in is temporarily unavailable") from exc

    return dependency
