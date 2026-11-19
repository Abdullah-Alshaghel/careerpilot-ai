from __future__ import annotations

import base64
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from identity_service.main import create_app
from identity_service.models import Base
from identity_service.settings import IdentitySettings


def signing_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


async def _ok() -> None:
    return None


async def _down() -> None:
    raise OSError("unreachable")


def _client(checks) -> TestClient:
    app = create_app(IdentitySettings(environment="test", jwt_signing_key=signing_key()))
    # A real engine that never connects: any test that reaches the database fails loudly.
    app.state.engine = create_async_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:9/none")
    app.state.health_checks = checks
    return TestClient(app)


def test_live():
    with _client({}) as client:
        resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["service"] == "identity-service"


def test_ready_reports_each_dependency():
    with _client({"postgres": _ok, "rabbitmq": _down}) as client:
        resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert {c["name"]: c["status"] for c in resp.json()["checks"]} == {"postgres": "up", "rabbitmq": "down"}


def test_settings_repr_never_contains_secrets():
    key = signing_key()
    settings = IdentitySettings(database_url="postgresql+psycopg://u:topsecret@h/db", jwt_signing_key=key)
    for text in (repr(settings), str(settings.model_dump())):
        assert "topsecret" not in text
        assert key not in text


def test_owned_tables():
    # Data ownership guard: this service owns exactly these tables.
    assert set(Base.metadata.tables) == {"users", "sessions"}


def test_refuses_to_start_without_a_signing_key():
    with pytest.raises(RuntimeError, match="JWT_SIGNING_KEY"):
        create_app(IdentitySettings(environment="test"))


@pytest.mark.parametrize("bad", ["not base64 !!", base64.urlsafe_b64encode(b"short").decode()])
def test_refuses_to_start_with_a_malformed_signing_key(bad):
    with pytest.raises(ValueError, match="JWT_SIGNING_KEY"):
        create_app(IdentitySettings(environment="test", jwt_signing_key=bad))


def test_jwks_publishes_only_the_public_key():
    with _client({}) as client:
        resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    (jwk,) = resp.json()["keys"]
    assert set(jwk) == {"kty", "crv", "x", "kid", "alg", "use"}
    assert "d" not in jwk  # the private part of an OKP JWK


def test_me_without_token_is_401_before_touching_the_database():
    with _client({}) as client:
        resp = client.get("/v1/users/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert resp.json()["error"]["code"] == "unauthorized"


def test_me_with_forged_token_is_401():
    with _client({}) as client:
        resp = client.get("/v1/users/me", headers={"Authorization": "Bearer a.b.c"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/auth/register", {"email": "not-an-email", "password": "long enough password"}),
        ("/v1/auth/register", {"email": "a@example.com", "password": "short"}),
        ("/v1/auth/register", {"email": "a@example.com", "password": "x" * 129}),
        ("/v1/auth/register", {"email": "a@example.com", "password": "long enough password", "role": "admin"}),
        ("/v1/auth/login", {"email": "a@example.com"}),
        ("/v1/auth/refresh", {}),
    ],
)
def test_invalid_bodies_are_422_without_echoing_values(path, body):
    with _client({}) as client:
        resp = client.post(path, json=body)
    assert resp.status_code == 422
    assert "long enough password" not in resp.text
