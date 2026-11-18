# matching-service

**Status:** planned — implemented in Phase 6 (see `docs/IMPLEMENTATION_STATUS.md`).

**Responsibility:** Explainable fit score: skills 30%, experience 25%, responsibilities 20%, career direction 15%, location/preferences 10%; hard constraints separated from soft preferences.

**Owns data:** job_matches, score components. No other service reads these tables directly; other services use this service's API or its events.

**Built from the service template:** same layout as `services/identity-service` — `careerpilot_common.create_service_app`, `/health/live` + `/health/ready`, own Postgres database and role (`infrastructure/postgres/init`), Alembic migrations, Dockerfile, pytest suite.
