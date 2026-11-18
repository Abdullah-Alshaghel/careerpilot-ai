from __future__ import annotations

from fastapi.testclient import TestClient

from identity_service.main import create_app
from identity_service.models import Base
from identity_service.settings import IdentitySettings


class FakeEngine:
    async def dispose(self) -> None:
        return None


async def _ok() -> None:
    return None


async def _down() -> None:
    raise OSError("unreachable")


def _client(checks) -> TestClient:
    app = create_app(IdentitySettings(environment="test"))
    app.state.engine = FakeEngine()
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
    settings = IdentitySettings(database_url="postgresql+psycopg://u:topsecret@h/db")
    assert "topsecret" not in repr(settings)
    assert "topsecret" not in str(settings.model_dump())


def test_owned_tables():
    # Data ownership guard: this service owns exactly these tables.
    assert set(Base.metadata.tables) == {"users", "sessions"}
