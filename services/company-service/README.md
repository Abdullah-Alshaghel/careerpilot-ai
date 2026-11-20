# company-service

**Status:** implemented in Phase 4.

**Responsibility:** the user's target companies (name, careers page, region, priority, notes) and their *job sources* (connectors): where the Job Discovery Service (Phase 5) will look for openings.

**Owns data:** `companies`, `connectors` (database `companies`, role `companies_svc`).

## API (through the gateway, bearer token required)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/v1/companies` | sorted by priority (high → low), then name |
| `POST` | `/v1/companies` | `409 company_exists` (same name, case/punctuation-insensitive), `409 company_limit` (200) |
| `GET` / `PATCH` / `DELETE` | `/v1/companies/{id}` | `PATCH` is partial; send `version` to reject a stale edit with `409 version_conflict` |
| `POST` | `/v1/companies/{id}/connectors` | `409 connector_exists`, `409 connector_limit` (10) |
| `PATCH` / `DELETE` | `/v1/companies/{id}/connectors/{connector_id}` | enable/pause, change config, remove |

Every response is the full company (connectors included), plus `detected_connector`: a public ATS board recognised from `careers_url` (Greenhouse, Lever, Ashby) that hasn't been added yet.

## Connector types (public identifiers only)

| Type | Config | Example |
|---|---|---|
| `careers_page` | `{"url": "https://…"}` | `https://acme.com/careers` |
| `greenhouse` | `{"board_token": "acme"}` | `boards.greenhouse.io/acme` |
| `lever` | `{"company": "acme"}` | `jobs.lever.co/acme` |
| `ashby` | `{"organization": "acme"}` | `jobs.ashbyhq.com/acme` |

Configs with any other key (an API key, a password) are refused. Nothing is fetched in this phase.

## URL safety

Careers URLs and careers-page connectors must be `https://` on a public DNS name and the default port, with no username/password; IP literals (including decimal tricks), `localhost` and internal suffixes (`.local`, `.internal`, `.corp`, …) are refused with a specific code (`https_required`, `ip_address_not_allowed`, `private_host`, …). This blocks server-side request forgery targets *at entry*; the Phase 5 fetcher must still check the resolved IP before every request.
