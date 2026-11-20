"""Integration tests against the running docker compose stack.

Run with:  CAREERPILOT_INTEGRATION=1 .venv/Scripts/python -m pytest tests/integration
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, text

from tests.integration.conftest import GATEWAY, SERVICE_DATABASES, admin_url, env, run_alembic

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]


def test_gateway_reports_all_dependencies_up():
    resp = httpx.get(f"{GATEWAY}/v1/health", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok", body
    assert {c["name"] for c in body["checks"]} >= {
        "redis",
        "identity-service",
        "document-service",
        "career-profile-service",
        "company-service",
    }


def test_cors_preflight_for_desktop_and_web_origins():
    for origin in ("http://localhost:3000", "http://localhost:1420", "http://tauri.localhost", "tauri://localhost"):
        resp = httpx.options(
            f"{GATEWAY}/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                # Exactly what @careerpilot/api-client sends on an authenticated request.
                "Access-Control-Request-Headers": "authorization,content-type,x-client-type,x-correlation-id",
            },
            timeout=10,
        )
        assert resp.status_code == 200, origin
        assert resp.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(("service", "db", "role", "password_var", "head"), SERVICE_DATABASES)
def test_migrations_applied_on_the_running_stack(service, db, role, password_var, head):
    engine = create_engine(admin_url(db))
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    assert version == head


@pytest.mark.parametrize(("service", "db", "role", "password_var", "head"), SERVICE_DATABASES)
def test_migrations_roundtrip_and_match_models_on_scratch_database(service, db, role, password_var, head):
    """upgrade -> alembic check (models == schema) -> downgrade -> upgrade, on a throwaway DB."""
    scratch = f"{db}_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{scratch}"'))
    try:
        url = admin_url(scratch)
        for step in (("upgrade", "head"), ("check",), ("downgrade", "base"), ("upgrade", "head")):
            run_alembic(url, *step, service=service)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.parametrize(("service", "db", "role", "password_var", "head"), SERVICE_DATABASES)
def test_service_role_cannot_access_other_databases(service, db, role, password_var, head):
    """Data ownership: each service role reaches its own database and no other."""
    password = env(password_var)
    port = env("POSTGRES_HOST_PORT")
    own = create_engine(f"postgresql+psycopg://{role}:{password}@127.0.0.1:{port}/{db}?connect_timeout=5")
    with own.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
    own.dispose()
    for other in ["postgres"] + [d for _, d, *_ in SERVICE_DATABASES if d != db]:
        engine = create_engine(f"postgresql+psycopg://{role}:{password}@127.0.0.1:{port}/{other}?connect_timeout=5")
        with pytest.raises(Exception, match="permission denied"):
            with engine.connect():
                pass
        engine.dispose()
