# document-service

**Status:** planned — implemented in Phase 3 (see `docs/IMPLEMENTATION_STATUS.md`).

**Responsibility:** Secure upload (PDF/DOCX/TXT), malware/type/size checks, text extraction workers, versions. Binaries in S3 object storage; metadata in Postgres.

**Owns data:** documents, document_extractions. No other service reads these tables directly; other services use this service's API or its events.

**Built from the service template:** same layout as `services/identity-service` — `careerpilot_common.create_service_app`, `/health/live` + `/health/ready`, own Postgres database and role (`infrastructure/postgres/init`), Alembic migrations, Dockerfile, pytest suite.
