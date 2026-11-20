from __future__ import annotations

from pydantic import Field, SecretStr

from careerpilot_common.config import ServiceSettings


class DocumentSettings(ServiceSettings):
    service_name: str = "document-service"

    # postgresql+psycopg://documents_svc:<password>@postgres:5432/documents
    database_url: SecretStr = SecretStr("postgresql+psycopg://documents_svc:documents@127.0.0.1:55432/documents")
    rabbitmq_url: SecretStr = SecretStr("amqp://careerpilot:careerpilot@127.0.0.1:5672/careerpilot")
    db_pool_size: int = 5

    # Token verification: Identity's internal JWKS endpoint (never via the gateway).
    identity_jwks_url: str = "http://127.0.0.1:8101/.well-known/jwks.json"

    # S3-compatible object storage. The bucket is private; objects are keyed
    # by user and document id, never by the uploaded filename.
    s3_endpoint_url: str = "http://127.0.0.1:8333"
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = "careerpilot-documents"

    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_documents_per_user: int = Field(default=50, ge=1)

    # Extraction worker limits (each file is processed in a separate process).
    extraction_timeout_seconds: float = Field(default=30, gt=0, le=300)
    extraction_memory_limit_mb: int = Field(default=512, ge=64)
    max_pdf_pages: int = Field(default=50, ge=1)
    # Text handed to the career-profile service in the event payload.
    max_extracted_chars: int = Field(default=200_000, ge=1000)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    # A document stuck in "processing" this long (worker crashed) is retried.
    stale_processing_seconds: int = Field(default=300, ge=30)
    max_extraction_attempts: int = Field(default=3, ge=1)
    worker_heartbeat_file: str = "/tmp/document-worker.heartbeat"  # noqa: S108 - container-local
