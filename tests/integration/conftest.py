from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

# psycopg's async driver can't run on Windows' default Proactor event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = os.environ.get("CAREERPILOT_GATEWAY_URL", "http://127.0.0.1:8080")


def env(name: str) -> str:
    """Read from the process env, falling back to the repo's .env file."""
    if name in os.environ:
        return os.environ[name]
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise KeyError(name)


def admin_url(db: str) -> str:
    return (
        f"postgresql+psycopg://{env('POSTGRES_USER')}:{env('POSTGRES_PASSWORD')}"
        f"@127.0.0.1:{env('POSTGRES_HOST_PORT')}/{db}?connect_timeout=5"
    )


# (service directory, database, owning role, password variable, migration head)
SERVICE_DATABASES = [
    ("identity-service", "identity", "identity_svc", "IDENTITY_DB_PASSWORD", "0002"),
    ("document-service", "documents", "documents_svc", "DOCUMENTS_DB_PASSWORD", "0001"),
    ("career-profile-service", "career_profile", "career_profile_svc", "CAREER_PROFILE_DB_PASSWORD", "0001"),
    ("company-service", "companies", "companies_svc", "COMPANIES_DB_PASSWORD", "0001"),
]


def run_alembic(url: str, *args: str, service: str = "identity-service") -> None:
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-x", f"url={url}", *args],
        cwd=ROOT / "services" / service,
        check=True,
        capture_output=True,
    )


def scratch_database(service: str):
    """Context manager: a throwaway database migrated to the service's head."""
    from contextlib import contextmanager

    @contextmanager
    def manager():
        name = f"{service.split('-')[0]}_test_{uuid.uuid4().hex[:8]}"
        admin = create_engine(admin_url("postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            url = admin_url(name)
            run_alembic(url, "upgrade", "head", service=service)
            yield url
        finally:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            admin.dispose()

    return manager()


@pytest.fixture(scope="module")
def scratch_identity_db() -> Iterator[str]:
    """A throwaway, fully migrated identity database; yields its URL."""
    with scratch_database("identity-service") as url:
        yield url


@pytest.fixture(scope="module")
def scratch_profile_db() -> Iterator[str]:
    with scratch_database("career-profile-service") as url:
        yield url


@pytest.fixture(scope="module")
def scratch_companies_db() -> Iterator[str]:
    with scratch_database("company-service") as url:
        yield url


@pytest.fixture(scope="module")
def scratch_documents_db() -> Iterator[str]:
    with scratch_database("document-service") as url:
        yield url
