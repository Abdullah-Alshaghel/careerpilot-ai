# Implementation status

_Last updated: 2026-11-20 (end of Phase 4)_

| Phase | Status |
|---|---|
| 1 — Foundation | ✅ Done |
| 2 — Identity | ✅ Done |
| 3 — Career Intelligence (CV upload, extraction, review) | ✅ Done |
| 4 — Company Workspace | ✅ **Done**: acceptance criteria met (see below) |
| 5 — Job Discovery | ⏳ Next |
| 6–10 | Not started |

## Phase 4 acceptance: "Full CRUD for companies"

| Check | Evidence (executed 2026-11-20) |
|---|---|
| Create / read / update / delete | Live through the gateway (`test_companies_stack.py`) and in a real browser (`companies.live.spec.ts`, desktop and mobile viewports): create, refuse an unsafe URL, edit priority, reload (persisted), delete |
| Job sources (connectors) | Add from the detected Lever board, pause, persisted across reload; Greenhouse/Lever/Ashby/careers-page configs validated (public identifiers only) |
| URL safety | 37 URL tests (cloud-metadata IP, decimal IPv4, `user@host`, internal suffixes, lookalike ATS hosts); the browser test sees the server refuse `169.254.169.254` with a clear reason |
| Concurrent edits | Stale version → `409 version_conflict` (integration, live, UI) |
| Isolation | Another user gets 404 for every company/connector route and an empty list; `companies_svc` refused by every other database |
| **Web** | Playwright live spec above (Next.js production build); careers page opens in a new tab |
| **Desktop** | Same shared screen; careers pages open in the system browser through the opener plugin, capability scoped to `https://*` (security test). The plugin and capability compile and validate (`cargo test`). **Release installer not rebuilt for Phase 4:** the build was stopped because the machine ran out of memory; rerun `npm run build:desktop` with more free memory |

## Test results after Phase 4 (actually executed)

| Suite | Result |
|---|---|
| Python unit + contract | 267 passed |
| Python integration (`CAREERPILOT_INTEGRATION=1`) | 78 passed (4 services: migrations, role isolation; auth, documents, profile, outbox, companies) |
| TypeScript unit (Vitest) | 158 passed: api-client 40, ui 48, validation 46, web 17, i18n 4, desktop 3 |
| Web E2E (Playwright, 2 projects) | 30 passed, run twice back to back |
| Desktop Rust (`cargo test`) | 9 passed (1 unit + 8 security-boundary) |
| Lint/format/types/contracts | all clean |

## Bugs found and fixed during Phase 4

- **Windows line endings broke a container script.** Edit scripts on Windows wrote CRLF; the Postgres provisioning script, mounted directly into a Linux container, then failed (`set: illegal option -`) and the stack would not start. All 43 affected files were converted to LF, and the repo scripts that write files (`init-env.py`, `export-contracts.py`) now write exact LF bytes on every OS.
- An ambiguous UI test assertion matched a dropdown option instead of the saved value; fixed to check the saved card.

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

## Test results after Phase 3 (actually executed)

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

## Next implementation step (Phase 5: Job Discovery)

1. `job-discovery-service`: search runs started manually by the user (`search.started`), connectors for the public Greenhouse, Lever and Ashby job-board APIs and careers pages, normalization into `jobs` with source links, deduplication on `(source, external_id)` plus a description hash.
2. A safe fetcher: resolve each host and refuse private, loopback and link-local addresses on every request and redirect (ADR-021); timeouts, size caps, per-source rate limits, robots/terms review per connector.
3. Events `job.discovered`, consumer idempotency; Job Discovery screen (list, filters, source links), English and Arabic.
4. Tests: critical tests #5 and #6, connector fixtures (no live third-party calls in CI), isolation, E2E.
