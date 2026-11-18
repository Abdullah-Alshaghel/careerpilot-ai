import asyncio
import uuid

from fastapi import APIRouter
from fastapi.testclient import TestClient

from careerpilot_common.app import create_service_app
from careerpilot_common.config import ServiceSettings
from careerpilot_common.errors import ServiceError


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("db down at postgresql://u:secret@h/db")


async def _slow() -> None:
    await asyncio.sleep(5)


def _client(checks: dict) -> TestClient:
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    @router.get("/expected")
    async def expected() -> None:
        raise ServiceError(409, "conflict", "Already exists")

    settings = ServiceSettings(service_name="test-svc", environment="test", health_check_timeout_seconds=0.2)
    app = create_service_app(settings, checks=lambda: checks, routers=[router])
    return TestClient(app)


def test_live_never_runs_checks():
    resp = _client({"db": _fail}).get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["checks"] == []


def test_ready_ok_when_all_checks_pass():
    resp = _client({"db": _ok, "cache": _ok}).get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert {c["name"] for c in body["checks"]} == {"db", "cache"}


def test_ready_degraded_returns_503_without_leaking_error_text():
    resp = _client({"db": _fail, "cache": _ok}).get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    db = next(c for c in body["checks"] if c["name"] == "db")
    assert db == {"name": "db", "status": "down", "latency_ms": db["latency_ms"], "detail": "ConnectionError"}
    assert "secret" not in resp.text


def test_ready_check_timeout_is_reported():
    resp = _client({"slow": _slow}).get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"][0]["detail"] == "timeout"
    assert resp.json()["status"] == "down"


def test_correlation_id_is_generated_and_echoed():
    client = _client({})
    generated = client.get("/health/live").headers["x-correlation-id"]
    uuid.UUID(generated)
    echoed = client.get("/health/live", headers={"X-Correlation-ID": "client-abc-123"})
    assert echoed.headers["x-correlation-id"] == "client-abc-123"


def test_malformed_correlation_id_is_replaced():
    resp = _client({}).get("/health/live", headers={"X-Correlation-ID": "bad\nvalue"})
    assert resp.headers["x-correlation-id"] != "bad\nvalue"
    uuid.UUID(resp.headers["x-correlation-id"])


def test_unhandled_error_is_generic_and_keeps_correlation_id():
    resp = _client({}).get("/boom", headers={"X-Correlation-ID": "trace-0001-abcd"})
    assert resp.status_code == 500
    assert resp.headers["x-correlation-id"] == "trace-0001-abcd"
    assert resp.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred",
            "correlation_id": "trace-0001-abcd",
        }
    }
    assert "internal detail" not in resp.text


def test_access_log_records_allow_listed_client_type(caplog):
    client = _client({})
    with caplog.at_level("INFO", logger="careerpilot.access"):
        client.get("/health/live", headers={"X-Client-Type": "desktop"})
        client.get("/health/live", headers={"X-Client-Type": "<script>evil"})
    types = [r.client_type for r in caplog.records if r.name == "careerpilot.access"]
    assert types == ["desktop", "other"]


def test_service_error_shape():
    resp = _client({}).get("/expected")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


def test_unknown_route_uses_error_shape():
    resp = _client({}).get("/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_docs_disabled_in_production():
    settings = ServiceSettings(service_name="p", environment="production")
    client = TestClient(create_service_app(settings))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
