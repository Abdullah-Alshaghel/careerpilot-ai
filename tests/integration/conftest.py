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


def run_alembic(url: str, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-x", f"url={url}", *args],
        cwd=ROOT / "services" / "identity-service",
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def scratch_identity_db() -> Iterator[str]:
    """A throwaway, fully migrated identity database; yields its URL."""
    name = f"identity_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        url = admin_url(name)
        run_alembic(url, "upgrade", "head")
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
