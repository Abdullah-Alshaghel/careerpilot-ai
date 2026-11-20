from __future__ import annotations

from pydantic import Field, SecretStr

from careerpilot_common.config import ServiceSettings


class DiscoverySettings(ServiceSettings):
    service_name: str = "job-discovery-service"

    # postgresql+psycopg://jobs_svc:<password>@postgres:5432/jobs
    database_url: SecretStr = SecretStr("postgresql+psycopg://jobs_svc:jobs@127.0.0.1:55432/jobs")
    rabbitmq_url: SecretStr = SecretStr("amqp://careerpilot:careerpilot@127.0.0.1:5672/careerpilot")
    db_pool_size: int = 5
    identity_jwks_url: str = "http://127.0.0.1:8101/.well-known/jwks.json"
    # Companies and their job sources are read from the Company Service with
    # the caller's own token (its data; never its database).
    company_service_url: str = "http://127.0.0.1:8104"

    # Search runs are started by the user, one at a time, at most this often.
    search_cooldown_seconds: int = Field(default=60, ge=0)
    max_sources_per_run: int = Field(default=50, ge=1)
    max_jobs_per_source: int = Field(default=500, ge=1)

    # Outbound fetching (see fetcher.py).
    fetch_timeout_seconds: float = Field(default=15, gt=0, le=60)
    fetch_max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    fetch_host_interval_seconds: float = Field(default=1.0, ge=0)

    # The search worker runs inside this process by default (one container on
    # small machines); runs are claimed with SKIP LOCKED, so replicas are safe.
    run_worker: bool = True
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    stale_run_seconds: int = Field(default=900, ge=60)
    max_run_attempts: int = Field(default=2, ge=1)
