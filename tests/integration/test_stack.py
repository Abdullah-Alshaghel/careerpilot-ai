"""Integration tests against the running docker compose stack.

Run with:  CAREERPILOT_INTEGRATION=1 .venv/Scripts/python -m pytest tests/integration
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.integration.conftest import GATEWAY, admin_url, env, run_alembic

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]


def test_gateway_reports_all_dependencies_up():
    resp = httpx.get(f"{GATEWAY}/v1/health", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok", body
    assert {c["name"] for c in body["checks"]} >= {"redis", "identity-service"}


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


def test_identity_migrations_applied_and_in_sync_with_models():
    engine = create_engine(admin_url("identity"))
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    assert {"users", "sessions", "alembic_version"} <= tables
    assert version == "0002"


def test_migrations_roundtrip_and_match_models_on_scratch_database():
    """upgrade -> alembic check (models == schema) -> downgrade, on a throwaway DB."""
    scratch = f"identity_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{scratch}"'))
    try:
        url = admin_url(scratch)

        def alembic(*args: str) -> None:
            run_alembic(url, *args)

        alembic("upgrade", "head")
        alembic("check")  # fails if models and migrations diverge
        alembic("downgrade", "base")
        alembic("upgrade", "head")
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        admin.dispose()


def test_service_role_cannot_access_other_databases():
    """Data ownership: identity_svc must not connect to the postgres admin DB."""
    url = (
        f"postgresql+psycopg://identity_svc:{env('IDENTITY_DB_PASSWORD')}"
        f"@127.0.0.1:{env('POSTGRES_HOST_PORT')}/postgres?connect_timeout=5"
    )
    engine = create_engine(url)
    with pytest.raises(Exception, match="permission denied"):
        with engine.connect():
            pass
    engine.dispose()
