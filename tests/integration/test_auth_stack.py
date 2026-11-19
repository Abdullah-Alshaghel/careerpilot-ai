"""Authentication end to end through the real gateway, Redis and Identity
containers. Run with CAREERPILOT_INTEGRATION=1."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
import redis

from tests.integration.conftest import GATEWAY, env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True, scope="module")
def reset_rate_limits():
    """Every local test shares one source IP; start from clean counters so
    repeated runs don't trip the per-IP limits."""
    client = redis.Redis(host="127.0.0.1", port=6379, password=env("REDIS_PASSWORD"))
    for key in client.scan_iter("rl:*"):
        client.delete(key)
    client.close()


@pytest.fixture
def api():
    with httpx.Client(base_url=GATEWAY, timeout=15, headers={"x-client-type": "desktop"}) as client:
        yield client


def register(api: httpx.Client, email: str | None = None) -> dict:
    resp = api.post(
        "/v1/auth/register", json={"email": email or f"stack-{uuid.uuid4().hex[:10]}@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_desktop_session_lifecycle_through_the_gateway(api):
    tokens = register(api)
    me = api.get("/v1/users/me", headers=bearer(tokens))
    assert me.status_code == 200
    assert me.json()["id"] == tokens["user"]["id"]
    assert me.headers["x-correlation-id"]

    refreshed = api.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    rotated = refreshed.json()

    assert api.post("/v1/auth/logout", json={"refresh_token": rotated["refresh_token"]}).status_code == 204
    assert api.post("/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 401
    assert api.get("/v1/users/me", headers=bearer(rotated)).status_code == 401


def test_protected_route_requires_a_token_at_the_edge(api):
    resp = api.get("/v1/users/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_user_cannot_impersonate_another_user_via_headers(api):
    alice, bob = register(api), register(api)
    resp = api.get(
        "/v1/users/me",
        headers={**bearer(alice), "X-Authenticated-User-Id": bob["user"]["id"], "X-User-Id": bob["user"]["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == alice["user"]["id"]


def test_tampered_token_is_rejected(api):
    tokens = register(api)
    header, payload, signature = tokens["access_token"].split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    resp = api.get("/v1/users/me", headers={"Authorization": f"Bearer {header}.{payload}.{flipped}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_repeated_failed_logins_lock_the_account(api):
    email = f"locked-{uuid.uuid4().hex[:10]}@example.com"
    register(api, email)
    try:
        limit = int(env("RATE_LIMIT_LOGIN_FAILURES_PER_ACCOUNT"))
    except KeyError:
        limit = 5  # docker-compose.yml default
    for _ in range(limit):
        assert api.post("/v1/auth/login", json={"email": email, "password": "wrong password!!"}).status_code == 401
    # Even the right password is refused until the window passes.
    locked = api.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"
    assert int(locked.headers["retry-after"]) > 0


def test_cors_preflight_for_auth_routes_from_every_client_origin(api):
    for origin in ("http://localhost:3000", "http://localhost:1420", "http://tauri.localhost", "tauri://localhost"):
        resp = api.options(
            "/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-client-type,x-correlation-id",
            },
        )
        assert resp.status_code == 200, origin
        assert resp.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in resp.headers


def test_jwks_is_not_exposed_through_the_gateway(api):
    assert api.get("/.well-known/jwks.json").status_code == 404
