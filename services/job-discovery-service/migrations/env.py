from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from job_discovery_service.models import Base
from job_discovery_service.settings import DiscoverySettings

target_metadata = Base.metadata


def _url() -> str:
    # Explicit -x url=... (used by tests) wins over environment configuration.
    return context.get_x_argument(as_dictionary=True).get("url") or DiscoverySettings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
