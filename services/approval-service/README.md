# approval-service

**Status:** planned — implemented in Phase 8 (see `docs/IMPLEMENTATION_STATUS.md`).

**Responsibility:** Job approval and final application approval gates, bound to package_version; invalidation on change; revocation.

**Owns data:** approval_events, approval policies. No other service reads these tables directly; other services use this service's API or its events.

**Built from the service template:** same layout as `services/identity-service` — `careerpilot_common.create_service_app`, `/health/live` + `/health/ready`, own Postgres database and role (`infrastructure/postgres/init`), Alembic migrations, Dockerfile, pytest suite.
