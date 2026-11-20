# Implementation status

_Last updated: 2026-11-20 (end of Phase 3)_

| Phase | Status |
|---|---|
| 1 — Foundation | ✅ Done |
| 2 — Identity | ✅ Done |
| 3 — Career Intelligence (CV upload, extraction, review) | ✅ **Done**: acceptance criteria met (see below) |
| 4 — Company Workspace | ⏳ Next |
| 5–10 | Not started |

## Phase 3 acceptance: "Upload a file and review the information from Desktop and Web"

| Check | Evidence (executed 2026-11-20) |
|---|---|
| Upload → extraction → suggested facts | Live: real browser uploads a CV through the gateway → Document Service → S3 → isolated extraction worker → outbox → RabbitMQ → Career Profile worker → review UI (Playwright `profile.live.spec.ts`, desktop and mobile viewports) |
| Review | Same spec: show source excerpt, reject one, approve the rest, reload → decisions persisted server-side; deleting the document keeps approved facts |
| PDF, Word and text | Integration: DOCX and PDF uploads extracted with correct title, organization, dates, skills (`test_documents_stack.py`); TXT in the browser test |
| English and Arabic CVs | Parser tests with full English and Arabic CVs (contact, experience, education, skills, languages, summary) |
| Hostile files | Disguised executable, PNG, macro-enabled DOCX, zip bomb, path traversal, empty, oversize and encrypted PDF all handled with a clear reason |
| Isolation | Another user gets 404 for documents and facts, sees an empty profile; every route needs a token; each service DB role refused by all other databases |
| **Web** | Playwright live spec above (Next.js production build) |
| **Desktop** | Same shared screens (`@careerpilot/ui`), same API client; desktop component tests pass; native file-drop disabled so HTML drag-and-drop reaches the page (security test). Release build (`CareerPilot AI_0.1.0_x64-setup.exe` 1.19 MiB, `.msi` 1.64 MiB, unsigned) launched: it restored the session from the Windows keychain and the gateway logged `GET /v1/profile 200 client_type=desktop` |

## Test results (actually executed)

| Suite | Result |
|---|---|
| Python unit + contract (`pytest`) | 207 passed (59 integration tests skip without the stack flag) |
| Python integration (`CAREERPILOT_INTEGRATION=1`) | 59 passed: migrations (3 services: applied, `alembic check`, round trip), DB role isolation, auth (Phase 2), profile rules against Postgres, outbox relay, live document pipeline |
| TypeScript unit (Vitest, 6 workspaces) | 127 passed: api-client 37, validation 34, ui 32, web 17, i18n 4, desktop 3 |
| Web E2E (Playwright, 2 projects) | 28 passed (22 mocked + 6 live), run twice back to back |
| Desktop Rust (`cargo test`) | 8 passed (1 unit + 7 security-boundary) |
| Lint/format/types | ruff ✔, ruff format ✔, ESLint (0 warnings) ✔, Prettier ✔, `tsc` across all workspaces ✔, generated contracts up to date ✔ |
| Bundle secret scan | passed (16 files; 12 private `.env` values) |
| Dead-letter queue / logs | DLQ empty after the live suites; 0 occurrences of test CV content in any container log |

## Critical-test coverage map

| # | Test | Status |
|---|---|---|
| 1 | Registration/login | ✅ Phase 2 |
| 2 | CV upload | ✅ type/size/macro/zip-bomb checks, dedupe, per-user storage keys |
| 3 | Extraction | ✅ PDF/DOCX/TXT, Arabic, isolated child process with timeout, failure reasons |
| 4 | Verification | ✅ facts start `extracted`; approve/reject/edit/add; conflicts shown, never auto-resolved; decisions persist |
| 5–6 | Discovery, duplicate detection | Phase 5 |
| 7–8 | Analysis, match score | Phase 6 |
| 9–10 | CV / cover-letter generation | Phase 7 |
| 11–14 | Job approval, final approval, submission blocking, approval invalidation | Phase 8 |
| 15 | User data isolation | ✅ identity, documents and profile (API, DB roles, cache cleared on sign-out) |
| 16 | Event idempotency | ✅ redelivered `document.text_extracted` processed once; outbox publishes each event once, in order, and survives a broker outage |
| 17 | Desktop security boundaries | ✅ plus native file drop disabled |

## What Phase 3 added

- **Document Service** (API + worker): content-sniffed uploads, private S3 storage, isolated extraction, outbox events.
- **Career Profile Service** (API + worker): rule-based English/Arabic CV parser, facts with source/excerpt/confidence/status, duplicates vs conflicts, idempotent event intake, profile versioning.
- **Shared:** `require_user` dependency, messaging (outbox, relay, consumer with dead-lettering), generated contracts (`scripts/export-contracts.py`).
- **Clients:** Career profile page (upload with drag-and-drop, document status, review by section, edit/add, approve all, conflicts banner, source excerpts), English and Arabic.
- **Infrastructure:** idempotent database provisioning on every `up` (new services get databases on existing volumes), worker heartbeats as health checks.

Design decisions: ADR-016 to ADR-020 in `ARCHITECTURE.md`.

## Bugs found and fixed during Phase 3

- **Conflicting dates were silently dropped.** A second CV listing the same job with different dates was treated as a duplicate. Duplicates now require matching details; differing details become a conflict for the user (ADR-020). Found by `test_second_document_adds_only_new_facts_and_flags_conflicts`.
- **Facts came back in random order** (same transaction timestamp). Added an insertion sequence; facts follow the CV's order.
- **RabbitMQ health check used 1–2 CPUs every 10 seconds** (`rabbitmq-diagnostics` starts an Erlang VM per run). Replaced with a TCP check; idle CPU went from spikes of 128–208% to under 3%.
- **Workers were reported unhealthy:** they inherited the HTTP health check from the service image. They now write heartbeats (the consumer's only while its broker connection is open).
- **Uploads shared the 15 s gateway timeout.** Uploads now get 60 s; upload logs record step timings.

## Known issues / limitations

- **Docker memory on this machine.** The Docker VM has 3.5 GB, shared with another project's 11 containers, and was ~650 MB into swap. Under parallel live E2E load, Argon2id registrations (64 MiB each) slowed from 0.3 s to 17–28 s and failed the run. Mitigated locally (2 concurrent hashes, 2 live E2E workers): two consecutive full E2E runs then passed with the slowest registration at 2.4 s. Giving Docker more memory (or stopping the other project while working) removes the constraint. One 15 s upload stall during that period could not be attributed to a cause.
- **No malware scanning or OCR yet** (see `SECURITY.md`, Phase 3 limitations).
- **Desktop end-to-end in a real window** is still component tests + launch check; `tauri-driver` WebDriver E2E is planned for Phase 9.
- Unsigned desktop builds; production API host placeholder; Redis licensing note (unchanged from earlier phases).
- `/v1/health` fans out to every service on each call; add a short result cache before production.

## Next implementation step (Phase 4: Company Workspace)

1. `company-service` from the service template: `companies` (name, careers URL, region, priority) and `connectors` (type, config ref, enabled), owned per user.
2. Full CRUD API with validation (HTTPS-only URLs, no credentials in connector config: references only), events `company.created/updated/deleted`.
3. Company workspace screen in `@careerpilot/ui` (list, add/edit, priority, connector status), English/Arabic.
4. Tests: CRUD, URL validation, isolation (#15), contracts, live E2E.
