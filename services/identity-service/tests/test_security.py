from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from argon2 import PasswordHasher as Argon2Hasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from careerpilot_common.auth import KeySet, TokenError, verify_access_token
from identity_service.schemas import LoginRequest, RegisterRequest
from identity_service.security import PasswordHasher, TokenSigner, hash_refresh_token, new_refresh_token

# Cheap parameters keep the suite fast; production uses argon2-cffi defaults.
FAST = Argon2Hasher(time_cost=1, memory_cost=1024, parallelism=1)
ARABIC_PASSPHRASE = "  كلمة مرور طويلة جدا  "


async def test_password_hash_is_argon2id_and_verifies():
    hasher = PasswordHasher(hasher=FAST)
    hashed = await hasher.hash("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert "correct horse battery" not in hashed
    assert await hasher.verify(hashed, "correct horse battery") == (True, False)
    assert await hasher.verify(hashed, "wrong password!!") == (False, False)


async def test_unknown_user_and_corrupt_hash_fail_closed():
    hasher = PasswordHasher(hasher=FAST)
    assert await hasher.verify(None, "anything at all") == (False, False)
    assert await hasher.verify("not-a-hash", "anything at all") == (False, False)


async def test_weaker_stored_hash_is_flagged_for_rehash():
    old = Argon2Hasher(time_cost=1, memory_cost=512, parallelism=1).hash("correct horse battery")
    assert await PasswordHasher(hasher=FAST).verify(old, "correct horse battery") == (True, True)


def test_production_hasher_uses_argon2id_with_rfc9106_parameters():
    params = PasswordHasher()._hasher
    assert params.memory_cost >= 64 * 1024
    assert params.time_cost >= 3


def test_access_token_round_trip():
    signer = TokenSigner(Ed25519PrivateKey.generate(), timedelta(minutes=10))
    user, session = uuid.uuid4(), uuid.uuid4()
    token = signer.issue_access_token(user, session, datetime.now(timezone.utc))
    claims = verify_access_token(token, KeySet.from_jwks(signer.jwks()))
    assert (claims.user_id, claims.session_id) == (user, session)


def test_access_token_expires():
    signer = TokenSigner(Ed25519PrivateKey.generate(), timedelta(minutes=10))
    issued = datetime.now(timezone.utc) - timedelta(minutes=11)
    token = signer.issue_access_token(uuid.uuid4(), uuid.uuid4(), issued)
    with pytest.raises(TokenError) as err:
        verify_access_token(token, signer.key_set())
    assert err.value.code == "token_expired"


def test_refresh_tokens_are_random_and_only_hashes_are_comparable():
    a, b = new_refresh_token(), new_refresh_token()
    assert a != b and len(a) == 43
    assert hash_refresh_token(a) == hash_refresh_token(a)
    assert len(hash_refresh_token(a)) == 32
    for junk in ("", "short", "x" * 44, "a" * 42 + "!"):
        assert hash_refresh_token(junk) is None


def test_register_normalizes_email():
    body = RegisterRequest(email="  Ada.Lovelace@Example.COM ", password="correct horse battery")
    assert body.email == "ada.lovelace@example.com"
    assert body.locale == "en"


@pytest.mark.parametrize("password", ["short", " " * 20, "x" * 129])
def test_register_rejects_weak_or_oversized_passwords(password):
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@example.com", password=password)


def test_register_accepts_unicode_passphrases_unchanged():
    body = RegisterRequest(email="a@example.com", password=ARABIC_PASSPHRASE)
    assert body.password == ARABIC_PASSPHRASE


def test_register_rejects_unsupported_locale():
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@example.com", password="correct horse battery", locale="fr")


def test_login_normalizes_email_without_validating_format():
    assert LoginRequest(email=" Someone@EXAMPLE.com", password="x").email == "someone@example.com"
