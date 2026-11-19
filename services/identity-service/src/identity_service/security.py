"""Password hashing, access-token signing and refresh-token handling."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from careerpilot_common.auth import ACCESS_TOKEN_TYPE, ALGORITHM, AUDIENCE, ISSUER, KeySet, public_jwk

# ---------------------------------------------------------------- passwords


class PasswordHasher:
    """Argon2id (argon2-cffi defaults: RFC 9106 low-memory profile).

    Hashing is CPU- and memory-heavy, so it runs in worker threads and a
    semaphore caps how many run at once.
    """

    def __init__(self, concurrency: int = 4, hasher: Argon2Hasher | None = None) -> None:
        self._hasher = hasher or Argon2Hasher()
        self._slots = asyncio.Semaphore(concurrency)
        # Verified when an email is unknown, so "no such user" and "wrong
        # password" take the same time and can't be told apart.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    async def hash(self, password: str) -> str:
        async with self._slots:
            return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str | None, password: str) -> tuple[bool, bool]:
        """Return ``(matches, needs_rehash)``."""
        async with self._slots:
            return await asyncio.to_thread(self._verify_sync, password_hash, password)

    def _verify_sync(self, password_hash: str | None, password: str) -> tuple[bool, bool]:
        if password_hash is None:
            self._safe_verify(self._dummy_hash, password)
            return False, False
        if not self._safe_verify(password_hash, password):
            return False, False
        return True, self._hasher.check_needs_rehash(password_hash)

    def _safe_verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


# ---------------------------------------------------------- access tokens


def decode_signing_key(value: str) -> Ed25519PrivateKey:
    """``value`` is a 32-byte Ed25519 seed, base64url-encoded."""
    try:
        seed = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("JWT_SIGNING_KEY is not valid base64url") from exc
    if len(seed) != 32:
        raise ValueError("JWT_SIGNING_KEY must decode to exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


class TokenSigner:
    def __init__(self, private_key: Ed25519PrivateKey, access_token_ttl: timedelta) -> None:
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self._jwk = public_jwk(self._public_key)
        self.access_token_ttl = access_token_ttl

    @property
    def key_id(self) -> str:
        return self._jwk["kid"]

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [dict(self._jwk)]}

    def key_set(self) -> KeySet:
        return KeySet({self.key_id: self._public_key})

    def issue_access_token(self, user_id: uuid.UUID, session_id: uuid.UUID, now: datetime) -> str:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(user_id),
            "sid": str(session_id),
            "iat": int(now.timestamp()),
            "exp": int((now + self.access_token_ttl).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        headers = {"kid": self.key_id, "typ": ACCESS_TOKEN_TYPE}
        return jwt.encode(claims, self._private_key, algorithm=ALGORITHM, headers=headers)


# --------------------------------------------------------- refresh tokens

_REFRESH_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_refresh_token() -> str:
    """256 bits of randomness; only its SHA-256 hash is ever stored."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> bytes | None:
    """``None`` for strings that can't be one of our tokens (skips the DB)."""
    if not _REFRESH_TOKEN.match(token):
        return None
    return hashlib.sha256(token.encode("ascii")).digest()
