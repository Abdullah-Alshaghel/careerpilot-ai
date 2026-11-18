# company-service

**Status:** planned — implemented in Phase 4 (see `docs/IMPLEMENTATION_STATUS.md`).

**Responsibility:** Target companies, careers URLs, ATS connector configuration.

**Owns data:** companies, connectors. No other service reads these tables directly; other services use this service's API or its events.

**Built from the service template:** same layout as `services/identity-service` — `careerpilot_common.create_service_app`, `/health/live` + `/health/ready`, own Postgres database and role (`infrastructure/postgres/init`), Alembic migrations, Dockerfile, pytest suite.
