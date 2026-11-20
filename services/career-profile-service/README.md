# career-profile-service

**Status:** implemented in Phase 3.

**Responsibility:** the career profile as individual *career facts*, each with its source document, a supporting excerpt, a confidence score and a verification status (`extracted` → `user_verified` or `rejected`). Nothing is a fact until the user approves it.

**Owns data:** `career_profiles`, `career_facts`, `processed_events` (database `career_profile`, role `career_profile_svc`).

## API (through the gateway, bearer token required)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/v1/profile` | profile version, facts in document order, and `conflicts` |
| `POST` | `/v1/profile/facts` | add a fact by hand (`user_verified`, confidence 1) |
| `PATCH` | `/v1/profile/facts/{id}` | `value` (editing approves it) and/or `verification_status` |
| `POST` | `/v1/profile/facts/bulk-status` | approve or reject up to 500 facts |
| `DELETE` | `/v1/profile/facts/{id}` | |

Fact types and value shapes: `personal` (`full_name`, `email`, `phone`, `location`, `link`), `summary`, `employment`, `education`, `skill`, `certification`, `language`; see `schemas.py` and `shared/api-contracts/schemas/career-profile.v1.schema.json`.

## Processes

- **API** (`uvicorn --factory career_profile_service.main:create_app`).
- **Worker** (`python -m career_profile_service.worker`): consumes `document.text_extracted.v1` and `document.deleted.v1` from queue `career-profile.document-events`.

## Rules

- **Extraction** is rule-based (`parser.py`, English and Arabic headings, several common CV layouts). It is deterministic, runs offline and sends CV text nowhere. AI extraction is planned with the Phase 6 agents and will produce the same `extracted` suggestions.
- **Idempotent intake:** each event id is processed once (`processed_events`).
- **Re-extraction** replaces only that document's unreviewed suggestions. Approved or rejected facts stay, and rejected ones are never suggested again.
- **Duplicates vs conflicts:** the same thing with the same details is a duplicate and is dropped. The same thing with different details (other dates, another name or level) is kept and reported as a conflict for the user; the service never picks a winner.
- **Deleting a document** removes its unreviewed suggestions; approved facts keep their excerpt as evidence.
- Every change increments the profile `version`, so later phases can record which version an application was built from.
