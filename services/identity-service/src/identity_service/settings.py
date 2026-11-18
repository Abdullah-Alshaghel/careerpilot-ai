from __future__ import annotations

from pydantic import SecretStr

from careerpilot_common.config import ServiceSettings


class IdentitySettings(ServiceSettings):
    service_name: str = "identity-service"

    # postgresql+psycopg://identity_svc:<password>@postgres:5432/identity
    database_url: SecretStr = SecretStr("postgresql+psycopg://identity_svc:identity@127.0.0.1:55432/identity")
    rabbitmq_url: SecretStr = SecretStr("amqp://careerpilot:careerpilot@127.0.0.1:5672/careerpilot")
    db_pool_size: int = 5
