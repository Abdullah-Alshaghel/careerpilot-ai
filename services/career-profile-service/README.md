# career-profile-service

**Status:** planned — implemented in Phase 3 (see `docs/IMPLEMENTATION_STATUS.md`).

**Responsibility:** Career profile, career facts (with source, evidence, confidence, verification status), skills, preferences, profile versions.

**Owns data:** career_profiles, career_facts. No other service reads these tables directly; other services use this service's API or its events.

**Built from the service template:** same layout as `services/identity-service` — `careerpilot_common.create_service_app`, `/health/live` + `/health/ready`, own Postgres database and role (`infrastructure/postgres/init`), Alembic migrations, Dockerfile, pytest suite.
