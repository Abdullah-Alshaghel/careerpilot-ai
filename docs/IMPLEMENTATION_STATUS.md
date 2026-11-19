# Implementation status

_Last updated: 2026-11-20 (end of Phase 2)_

| Phase | Status |
|---|---|
| 1 — Foundation | ✅ Done |
| 2 — Identity | ✅ **Done**: acceptance criteria met (see below) |
| 3 — CV upload, extraction, verification | ⏳ Next |
| 4–10 | Not started |

## Phase 2 acceptance: "Users can register, sign in, stay signed in and sign out on Desktop and Web; data is isolated per user"

| Check | Evidence (executed 2026-11-19/20) |
|---|---|
| Identity API | `POST /v1/auth/register`, `/login`, `/refresh`, `/logout`, `GET /v1/users/me`, internal `GET /.well-known/jwks.json` |
| Real database | 21 in-process tests against a freshly migrated PostgreSQL database (`tests/integration/test_identity_flows.py`), including refresh-token reuse detection and a 5-way concurrent-refresh race |
| Through the gateway | 7 live tests via the running stack (`test_auth_stack.py`): session lifecycle, edge 401, header impersonation blocked, tampered token rejected, account lockout, CORS for all 4 client origins, JWKS not public |
| **Web** | Playwright live, desktop and mobile viewports: register in the UI → status `ok` → account page from `/v1/users/me` → HttpOnly cookie asserted → wrong password rejected → sign in → reload keeps the session → sign out → reload stays signed out |
| **Desktop** | Component tests drive the real session manager and keychain commands (mocked `invoke`): refresh token stored via `store_refresh_token`, never in web storage, cleared on sign-out. Rust: command validation and capability tests. Release build launched; gateway logged `GET /v1/health 200 client_type=desktop` |
| Desktop installable | `tauri build`: `CareerPilot AI_0.1.0_x64-setup.exe` (1.18 MiB), `CareerPilot AI_0.1.0_x64_en-US.msi` (1.63 MiB), **unsigned** |

## Test results (actually executed)

| Suite | Result |
|---|---|
| Python unit + contract (`pytest`) | 119 passed (30 integration tests skip without the stack flag) |
| Python integration (`CAREERPILOT_INTEGRATION=1`) | 30 passed |
| TypeScript unit (Vitest, 6 workspaces) | 97 passed: api-client 32, validation 23, ui 18, web 17, i18n 4, desktop 3 |
| Web E2E (Playwright, 2 projects) | 26 passed (22 mocked + 4 live), run twice back to back |
| Desktop Rust (`cargo test`) | 7 passed (1 unit + 6 security-boundary) |
| Lint/format/types | ruff ✔, ruff format ✔, ESLint (0 warnings) ✔, Prettier ✔, `tsc` across all workspaces ✔ |
| Bundle secret scan | passed (16 files; 10 private `.env` values, including `JWT_SIGNING_KEY`) |

## Critical-test coverage map

| # | Test | Status |
|---|---|---|
| 1 | Registration/login | ✅ Identity (DB), gateway (live), Web (live E2E), Desktop (component + keychain) |
| 2–4 | CV upload, extraction, verification | Phase 3 |
| 5–6 | Discovery, duplicate detection | Phase 5 (unique `(source, external_id)`) |
| 7–8 | Analysis, match score | Phase 6 |
| 9–10 | CV / cover-letter generation | Phase 7 |
| 11–14 | Job approval, final approval, submission blocking, approval invalidation | Phase 8 |
| 15 | User data isolation | ✅ for identity data: `/me` only resolves the token's user, spoofed identity headers dropped, forged/foreign-key tokens rejected, client cache cleared on sign-out. Each new service must scope queries by the verified `user_id` (SECURITY.md) |
| 16 | Event idempotency | Envelope + contract done; consumer idempotency in Phase 5 |
| 17 | Desktop security boundaries | ✅ `security.rs` now pins the 3 keychain permissions and the app command manifest |

## What Phase 2 added

- **Identity Service:** Argon2id passwords, Ed25519 access tokens (10 min), rotating refresh tokens (30-day absolute session) with reuse detection, migration `0002`.
- **Gateway:** token verification at the edge, identity-header stripping, Redis rate limits (per IP and per account), trusted-proxy handling for `X-Forwarded-For`.
- **Shared:** `careerpilot_common.auth` (verification + JWKS cache) for every future service; contracts `user.v1`, `auth-token.v1`, `auth-session.v1`.
- **Clients:** shared sign-in/registration screens (React Hook Form + Zod, English/Arabic, RTL), Account page, session manager with single-flight refresh and automatic retry on 401.
- **Desktop:** refresh token in the OS keychain via 3 scoped commands.
- **Web:** session endpoints holding the refresh token in an HttpOnly cookie (ADR-014).
- **Tooling:** `scripts/init-env.py` generates `.env` (and the signing key); CI uses it.

Design decisions: ADR-011 to ADR-015 in `ARCHITECTURE.md`.

## Bugs found and fixed during Phase 2

- **Readiness flapped under load.** Each probe opened a new RabbitMQ connection; during the live E2E run one handshake exceeded the 2 s timeout and the UI showed "degraded". Probes now share one connection (ADR-015); verified 15 health calls → 1 broker connection, and the E2E suite passes repeatedly.

## Known issues / limitations

- Security limitations of this phase (registration enumeration, fail-open rate limiting, logout latency for other services, single signing key, Windows Credential Manager model, web per-IP limits) are listed in [`SECURITY.md`](SECURITY.md#known-limitations-phase-2).
- **Desktop sign-in is not driven end to end in a real window.** Covered by component tests with the real session code and mocked `invoke`, Rust tests, and a launch check. Real keychain round-trips need `tauri-driver` WebDriver E2E (planned for Phase 9) or a manual check: sign in, quit, reopen → still signed in; entry visible in Credential Manager as `ai.careerpilot.desktop.dev`.
- Unsigned desktop builds; macOS not built (needs a Mac); CI not yet run (repository not on GitHub).
- Gateway `/v1/health` is public and fans out to every service's readiness check on each call; add a short result cache before production.
- The project lives in **OneDrive**; `node_modules`, `.venv` and `target/` sync poorly.
- Production API host in `tauri.production.conf.json` is a placeholder.
- Redis 8 licensing: consider Valkey or a managed Redis for production.

## Next implementation step (Phase 3: CV upload, extraction, verification)

1. `document-service` from the identity-service template: own database, verifies tokens with `careerpilot_common.auth`, every query scoped by `user_id`.
2. Uploads: presigned S3 PUT (revisit ADR-010's 12 MB gateway buffer), type sniffing by content, size caps, private bucket with per-user prefixes, no rendering of uploaded content.
3. Text extraction worker (PDF/DOCX) behind RabbitMQ, with extracted CV text redacted from logs.
4. Verification UI: the user reviews and corrects extracted fields before anything is used.
5. Tests: critical tests #2–4, plus cross-user access denial on documents (#15).
