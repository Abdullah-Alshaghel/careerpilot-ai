"""Identity Service against a real, freshly migrated PostgreSQL database.

Covers critical tests #1 (registration/login) and #15 (user data isolation)
at the service level. Run with CAREERPILOT_INTEGRATION=1.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from identity_service.main import create_app
from identity_service.settings import IdentitySettings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]

PASSWORD = "correct horse battery staple"
SIGNING_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def make_app(db_url: str, **overrides):
    settings = IdentitySettings(
        environment="test",
        database_url=db_url.replace("?connect_timeout=5", ""),
        **{"jwt_signing_key": SIGNING_KEY, **overrides},
    )
    app = create_app(settings)
    app.state.health_checks = {}
    return app


@pytest.fixture(scope="module")
def client(scratch_identity_db: str) -> Iterator[TestClient]:
    with TestClient(make_app(scratch_identity_db)) as c:
        yield c


@pytest.fixture(scope="module")
def db(scratch_identity_db: str):
    engine = create_engine(scratch_identity_db)
    yield engine
    engine.dispose()


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.com"


def register(client: TestClient, email: str | None = None, **headers) -> dict:
    resp = client.post("/v1/auth/register", json={"email": email or new_email(), "password": PASSWORD}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ------------------------------------------------------------ registration


def test_register_returns_a_session_and_stores_only_hashes(client, db):
    resp = client.post(
        "/v1/auth/register",
        json={"email": "  New.User@Example.com", "password": PASSWORD, "locale": "ar"},
        headers={"x-client-type": "desktop", "x-forwarded-for": "203.0.113.7", "user-agent": "CareerPilot/0.1"},
    )
    assert resp.status_code == 201
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert body["user"]["email"] == "new.user@example.com"
    assert body["user"]["locale"] == "ar"
    assert body["user"]["status"] == "active"

    with db.connect() as conn:
        user = conn.execute(text("SELECT * FROM users WHERE email = 'new.user@example.com'")).mappings().one()
        session = conn.execute(text("SELECT * FROM sessions WHERE user_id = :u"), {"u": user["id"]}).mappings().one()
    assert user["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in user["password_hash"]
    assert len(session["refresh_token_hash"]) == 32
    assert body["refresh_token"].encode() not in bytes(session["refresh_token_hash"])
    assert session["client_type"] == "desktop"
    assert str(session["ip_address"]) == "203.0.113.7"
    assert session["user_agent"] == "CareerPilot/0.1"


def test_duplicate_email_is_409_case_insensitively(client):
    email = new_email()
    register(client, email)
    resp = client.post("/v1/auth/register", json={"email": email.upper(), "password": PASSWORD})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_taken"


# ------------------------------------------------------------------- login


def test_login_success_and_generic_failures(client):
    email = new_email()
    register(client, email)

    ok = client.post("/v1/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert ok.status_code == 200
    assert ok.json()["user"]["email"] == email

    wrong = client.post("/v1/auth/login", json={"email": email, "password": "wrong password!!"})
    unknown = client.post("/v1/auth/login", json={"email": new_email(), "password": PASSWORD})
    # Unknown account and wrong password are indistinguishable to the caller.
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_each_login_is_a_separate_session(client):
    email = new_email()
    first = register(client, email)
    second = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()
    assert first["refresh_token"] != second["refresh_token"]
    # Logging out of one device leaves the other signed in.
    client.post("/v1/auth/logout", json={"refresh_token": first["refresh_token"]})
    assert client.get("/v1/users/me", headers=bearer(second)).status_code == 200
    assert client.get("/v1/users/me", headers=bearer(first)).status_code == 401


def test_disabled_account_cannot_sign_in_or_refresh(client, db):
    email = new_email()
    tokens = register(client, email)
    with db.begin() as conn:
        conn.execute(text("UPDATE users SET status = 'disabled' WHERE email = :e"), {"e": email})

    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 403
    assert login.json()["error"]["code"] == "account_disabled"
    assert client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.get("/v1/users/me", headers=bearer(tokens)).status_code == 401


# --------------------------------------------------- users/me and isolation


def test_me_returns_only_the_callers_own_account(client):
    alice, bob = register(client), register(client)
    me_alice = client.get("/v1/users/me", headers=bearer(alice))
    me_bob = client.get("/v1/users/me", headers=bearer(bob))
    assert me_alice.status_code == me_bob.status_code == 200
    assert me_alice.json() == alice["user"]
    assert me_bob.json() == bob["user"]
    assert me_alice.json()["id"] != me_bob.json()["id"]


def test_identity_headers_from_the_network_are_not_trusted(client):
    alice, bob = register(client), register(client)
    resp = client.get(
        "/v1/users/me",
        headers={**bearer(alice), "x-authenticated-user-id": bob["user"]["id"], "x-user-id": bob["user"]["id"]},
    )
    assert resp.json()["id"] == alice["user"]["id"]


def test_token_from_a_different_signing_key_is_rejected(scratch_identity_db, client):
    other_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    with TestClient(make_app(scratch_identity_db, jwt_signing_key=other_key)) as rogue:
        forged = register(rogue)  # a real token, from the wrong issuer key
    resp = client.get("/v1/users/me", headers=bearer(forged))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


# ----------------------------------------------------------------- refresh


def test_refresh_rotates_the_token(client):
    tokens = register(client)
    resp = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    rotated = resp.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert rotated["user"] == tokens["user"]
    assert client.get("/v1/users/me", headers=bearer(rotated)).status_code == 200
    # The session keeps its absolute expiry: refreshing never extends it.
    assert rotated["refresh_expires_in"] <= tokens["refresh_expires_in"]


def test_old_token_within_grace_window_is_superseded_not_theft(client):
    tokens = register(client)
    rotated = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    again = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "refresh_token_superseded"
    # The legitimate holder of the new token is unaffected.
    assert client.post("/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 200


def test_reuse_after_grace_window_revokes_the_whole_session(scratch_identity_db):
    with TestClient(make_app(scratch_identity_db, refresh_reuse_grace_seconds=0)) as client:
        stolen = register(client)
        rotated = client.post("/v1/auth/refresh", json={"refresh_token": stolen["refresh_token"]}).json()

        reuse = client.post("/v1/auth/refresh", json={"refresh_token": stolen["refresh_token"]})
        assert reuse.status_code == 401
        assert reuse.json()["error"]["code"] == "refresh_token_reused"
        # Both copies are now dead, and so is the session's access token.
        assert client.post("/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 401
        assert client.get("/v1/users/me", headers=bearer(rotated)).status_code == 401


def test_expired_session_cannot_refresh(client, db):
    tokens = register(client)
    with db.begin() as conn:
        conn.execute(
            text("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE user_id = :u"),
            {"u": tokens["user"]["id"]},
        )
    resp = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_refresh_token"


@pytest.mark.parametrize("token", ["", "garbage", "a" * 43, "x" * 256])
def test_unknown_refresh_tokens_are_401(client, token):
    resp = client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code in (401, 422)


async def test_concurrent_refreshes_rotate_exactly_once(scratch_identity_db):
    app = make_app(scratch_identity_db)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://identity") as client:
            reg = await client.post("/v1/auth/register", json={"email": new_email(), "password": PASSWORD})
            token = reg.json()["refresh_token"]
            results = await asyncio.gather(
                *(client.post("/v1/auth/refresh", json={"refresh_token": token}) for _ in range(5))
            )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 401, 401, 401, 401]
    assert {r.json()["error"]["code"] for r in results if r.status_code == 401} == {"refresh_token_superseded"}


# ------------------------------------------------------------------ logout


def test_logout_revokes_the_session_immediately_and_is_idempotent(client):
    tokens = register(client)
    assert client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/v1/auth/logout", json={"refresh_token": "unknown"}).status_code == 204
    assert client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    # The access token is still unexpired, but its session is gone.
    assert client.get("/v1/users/me", headers=bearer(tokens)).status_code == 401
