# job-discovery-service

**Status:** implemented in Phase 5.

**Responsibility:** user-started searches over the user's job sources, normalization of every posting into one shape, deduplication, single-link import, and the `job.discovered` event. The only service that reaches the internet.

**Owns data:** `search_runs`, `search_run_sources`, `jobs`, `outbox_events` (database `jobs`, role `jobs_svc`).

## API (through the gateway, bearer token required)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/searches` | `202`; snapshots the user's enabled job sources (read from the Company Service with the caller's own token). `422 no_sources`, `409 search_in_progress`, `429 search_too_soon` + `Retry-After` (60 s cooldown) |
| `GET` | `/v1/searches`, `/v1/searches/{id}` | status per source, with an error code when a source failed |
| `GET` | `/v1/jobs` | `q`, `company_id`, `remote_only`, `include_unlisted`, `include_duplicates`, `limit`, `offset`; newest first |
| `GET` | `/v1/jobs/{id}` | includes the description (plain text) |
| `POST` | `/v1/jobs/import` | `{"url": "https://…"}`; reads the page's schema.org `JobPosting`; `201` new, `200` known; `422 no_job_found` / `blocked_address` / `disallowed_by_robots` |

## Sources (architecture §9)

| Source | How |
|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` (official public API) |
| Lever | `GET api.lever.co/v0/postings/{company}?mode=json` (official public API) |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{org}` (official public API) |
| Careers page / link import | the page's schema.org `JobPosting` JSON-LD only; `robots.txt` honoured |

No LinkedIn, no logins, no general scraping. At most 500 jobs per source.

## Processing

- **Worker** runs inside the API process (`RUN_WORKER=true`) or alone (`python -m job_discovery_service.worker`); runs are claimed with `FOR UPDATE SKIP LOCKED`.
- **Upsert** on `(user, source, external_id)`: a re-found job is updated, not duplicated; a job missing from a successful fetch becomes `no_longer_listed` (and `active` again if it returns).
- **Cross-source duplicates:** the same company and the same title+description from another source → `duplicate_of_id`, hidden by default, no second `job.discovered`.
- **Descriptions** are converted from HTML to plain text (scripts/styles dropped, bidi-override characters removed) and never rendered as markup.

## Outbound safety (`fetcher.py`)

Every request: https only (every redirect hop re-checked, max 3), host resolved by the service and connected **only** to public addresses (private, loopback, link-local/metadata, CGNAT, multicast, reserved and IPv4-mapped forms refused at connect time, so DNS rebinding can't redirect it), 5 s connect / 15 s read timeouts, 5 MB response cap enforced while streaming, 1 s pacing per host, fixed User-Agent `CareerPilotAI-JobDiscovery/0.1`.
