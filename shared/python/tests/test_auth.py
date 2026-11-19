from __future__ import annotations

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from careerpilot_common.auth import (
    AUDIENCE,
    ISSUER,
    JwksCache,
    KeySet,
    TokenError,
    UnknownKeyError,
    bearer_token,
    public_jwk,
    verify_access_token,
)

KEY = Ed25519PrivateKey.generate()
OTHER_KEY = Ed25519PrivateKey.generate()
USER, SESSION = uuid.uuid4(), uuid.uuid4()


def make_token(key=KEY, *, kid=None, typ="at+jwt", alg="EdDSA", **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(USER),
        "sid": str(SESSION),
        "iat": now,
        "exp": now + 600,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    kid = kid or public_jwk(key.public_key())["kid"]
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid, "typ": typ})


KEYS = KeySet.from_public_keys(KEY.public_key())


def test_valid_token():
    claims = verify_access_token(make_token(), KEYS)
    assert claims.user_id == USER
    assert claims.session_id == SESSION


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"exp": int(time.time()) - 120}, "token_expired"),
        ({"aud": "someone-else"}, "invalid_token"),
        ({"iss": "attacker"}, "invalid_token"),
        ({"sid": None}, "invalid_token"),
        ({"sub": "not-a-uuid"}, "invalid_token"),
        ({"jti": None}, "invalid_token"),
    ],
)
def test_rejected_claims(overrides, code):
    with pytest.raises(TokenError) as err:
        verify_access_token(make_token(**overrides), KEYS)
    assert err.value.code == code


def test_signature_from_another_key_with_our_kid_is_rejected():
    kid = public_jwk(KEY.public_key())["kid"]
    with pytest.raises(TokenError):
        verify_access_token(make_token(OTHER_KEY, kid=kid), KEYS)


def test_unknown_kid():
    with pytest.raises(UnknownKeyError):
        verify_access_token(make_token(OTHER_KEY), KEYS)


def test_tampered_payload_is_rejected():
    header, payload, sig = make_token().split(".")
    forged = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(str(USER).encode(), str(uuid.uuid4()).encode())
    ).decode()
    with pytest.raises(TokenError):
        verify_access_token(f"{header}.{forged}.{sig}", KEYS)


def test_alg_none_and_hmac_confusion_are_rejected():
    kid = public_jwk(KEY.public_key())["kid"]
    unsigned = jwt.encode({"sub": str(USER)}, None, algorithm="none", headers={"kid": kid, "typ": "at+jwt"})
    hmac = jwt.encode({"sub": str(USER)}, "s" * 32, algorithm="HS256", headers={"kid": kid, "typ": "at+jwt"})
    for token in (unsigned, hmac):
        with pytest.raises(TokenError):
            verify_access_token(token, KEYS)


def test_wrong_token_type_is_rejected():
    with pytest.raises(TokenError):
        verify_access_token(make_token(typ="JWT"), KEYS)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "x" * 5000])
def test_garbage(token):
    with pytest.raises(TokenError):
        verify_access_token(token, KEYS)


def test_jwks_round_trip_ignores_unsupported_keys():
    doc = {
        "keys": [
            public_jwk(KEY.public_key()),
            {"kty": "RSA", "kid": "rsa", "n": "x", "e": "AQAB"},
            {"kty": "OKP", "crv": "Ed25519", "kid": "bad", "x": "!!"},
            "junk",
        ]
    }
    keys = KeySet.from_jwks(doc)
    assert len(keys) == 1
    verify_access_token(make_token(), keys)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("Bearer abc", "abc"), ("bearer  abc ", "abc"), ("Basic abc", None), ("Bearer ", None), (None, None)],
)
def test_bearer_token(header, expected):
    assert bearer_token(header) == expected


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_jwks_cache_refreshes_on_new_kid_but_rate_limits_refetches():
    clock, docs, calls = Clock(), [{"keys": [public_jwk(KEY.public_key())]}], []

    async def fetch():
        calls.append(clock.now)
        return docs[-1]

    cache = JwksCache(fetch, ttl=300, min_refresh_interval=30, clock=clock)
    await cache.verify(make_token())
    assert len(calls) == 1

    # Key rotation: a token signed with a new key triggers one refetch...
    docs.append({"keys": [public_jwk(KEY.public_key()), public_jwk(OTHER_KEY.public_key())]})
    clock.now += 31
    await cache.verify(make_token(OTHER_KEY))
    assert len(calls) == 2

    # ...but a burst of forged kids does not hammer Identity.
    for _ in range(5):
        with pytest.raises(UnknownKeyError):
            await cache.verify(make_token(Ed25519PrivateKey.generate()))
    assert len(calls) == 2


async def test_jwks_cache_keeps_serving_known_keys_when_identity_is_down():
    clock, state = Clock(), {"up": True}

    async def fetch():
        if not state["up"]:
            raise OSError("down")
        return {"keys": [public_jwk(KEY.public_key())]}

    cache = JwksCache(fetch, ttl=10, clock=clock)
    await cache.verify(make_token())
    state["up"], clock.now = False, clock.now + 60
    await cache.verify(make_token())  # stale but usable


async def test_jwks_cache_raises_when_nothing_was_ever_loaded():
    async def fetch():
        raise OSError("down")

    with pytest.raises(OSError):
        await JwksCache(fetch).verify(make_token())
