# application-service

**Status:** planned — implemented in Phase 7 (see `docs/IMPLEMENTATION_STATUS.md`).

**Responsibility:** Application packages: tailored CV, cover letter, answers, package versions, submission intents.

**Owns data:** applications, application_assets, application_answers. No other service reads these tables directly; other services use this service's API or its events.

**Built from the service template:** same layout as `services/identity-service` — `careerpilot_common.create_service_app`, `/health/live` + `/health/ready`, own Postgres database and role (`infrastructure/postgres/init`), Alembic migrations, Dockerfile, pytest suite.
