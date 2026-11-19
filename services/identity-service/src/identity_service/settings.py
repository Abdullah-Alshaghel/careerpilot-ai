from __future__ import annotations

from pydantic import Field, SecretStr

from careerpilot_common.config import ServiceSettings


class IdentitySettings(ServiceSettings):
    service_name: str = "identity-service"

    # postgresql+psycopg://identity_svc:<password>@postgres:5432/identity
    database_url: SecretStr = SecretStr("postgresql+psycopg://identity_svc:identity@127.0.0.1:55432/identity")
    rabbitmq_url: SecretStr = SecretStr("amqp://careerpilot:careerpilot@127.0.0.1:5672/careerpilot")
    db_pool_size: int = 5

    # Ed25519 private key seed: 32 random bytes, base64url without padding
    # (generate with `python scripts/init-env.py`). Required to serve auth
    # endpoints; optional here only so tooling such as Alembic can load the
    # settings without it.
    jwt_signing_key: SecretStr | None = None
    access_token_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    # Absolute session lifetime: a refresh token never extends it.
    refresh_token_ttl_seconds: int = Field(default=30 * 24 * 3600, ge=3600, le=90 * 24 * 3600)
    # A refresh token presented again within this window after rotation is
    # treated as a concurrent refresh (e.g. two browser tabs), not as theft.
    refresh_reuse_grace_seconds: int = Field(default=15, ge=0, le=120)
    # Argon2id uses ~64 MiB per hash; bound concurrent hashes to bound memory.
    password_hash_concurrency: int = Field(default=4, ge=1, le=64)
