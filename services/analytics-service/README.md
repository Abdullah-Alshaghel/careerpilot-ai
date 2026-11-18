# analytics-service

**Status:** planned — implemented in Phase 8 (see `docs/IMPLEMENTATION_STATUS.md`).

**Responsibility:** Funnel and outcome metrics from event read models.

**Owns data:** aggregates, read models. No other service reads these tables directly; other services use this service's API or its events.

**Built from the service template:** same layout as `services/identity-service` — `careerpilot_common.create_service_app`, `/health/live` + `/health/ready`, own Postgres database and role (`infrastructure/postgres/init`), Alembic migrations, Dockerfile, pytest suite.
