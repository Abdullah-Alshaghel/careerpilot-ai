from __future__ import annotations

from pydantic import SecretStr

from careerpilot_common.config import ServiceSettings


class ProfileSettings(ServiceSettings):
    service_name: str = "career-profile-service"

    # postgresql+psycopg://career_profile_svc:<password>@postgres:5432/career_profile
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://career_profile_svc:career_profile@127.0.0.1:55432/career_profile"
    )
    rabbitmq_url: SecretStr = SecretStr("amqp://careerpilot:careerpilot@127.0.0.1:5672/careerpilot")
    db_pool_size: int = 5
    identity_jwks_url: str = "http://127.0.0.1:8101/.well-known/jwks.json"
    # Durable queue for document events (one queue per consumer, docs/EVENTS.md).
    document_events_queue: str = "career-profile.document-events"
    worker_heartbeat_file: str = "/tmp/career-profile-worker.heartbeat"  # noqa: S108 - container-local
