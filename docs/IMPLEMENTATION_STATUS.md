# Implementation status

_Last updated: 2026-11-18 (end of Phase 1)_

| Phase | Status |
|---|---|
| 1 — Foundation | ✅ **Done**: acceptance criteria met (see below) |
| 2 — Identity | ⏳ Next. Schema (`users`, `sessions`) and migration already in place |
| 3–10 | Not started |

## Phase 1 acceptance: "Desktop and Web start and connect to the backend health endpoint"

| Check | Evidence (executed 2026-11-18) |
|---|---|
| Backend stack starts healthy | `docker compose up -d --build --wait` → exit 0; postgres, redis, rabbitmq, object-storage, identity-service, api-gateway healthy; identity-migrate exited 0 |
| Gateway health aggregates dependencies | `GET /v1/health` → `status: ok`, checks `redis` + `identity-service` (which checks postgres + rabbitmq) |
| **Web connects** | Playwright live spec: real Chromium → `http://localhost:3000` → gateway `/v1/health` 200, UI shows `ok` (desktop + mobile viewports) |
| **Desktop connects** | Release binary `careerpilot-desktop.exe` launched; gateway access log recorded `GET /v1/health 200 client_type=desktop` from the Tauri webview |
| Desktop installable | `tauri build` produced `CareerPilot AI_0.1.0_x64-setup.exe` (NSIS, 1.15 MiB) and `CareerPilot AI_0.1.0_x64_en-US.msi` (1.60 MiB), **unsigned** |

## Test results (actually executed)

| Suite | Result |
|---|---|
| Python unit + contract (`pytest`) | 42 passed, 5 skipped (the integration tests, which skip without the stack flag) |
| Python integration (`CAREERPILOT_INTEGRATION=1`) | 5 passed: gateway health, CORS preflight for all 4 client origins, migrations applied, `alembic check` + downgrade/upgrade round-trip, DB role isolation |
| TypeScript unit (Vitest, 5 packages/apps) | 27 passed |
| Web E2E (Playwright, 2 projects) | 10 passed (8 mocked + 2 live) |
| Desktop security (`cargo test`) | 4 passed |
| Lint/format/types | ruff ✔, ESLint (0 warnings) ✔, Prettier ✔, `tsc` across 7 workspaces ✔ |
| Bundle secret scan | passed (16 files; scanner verified to fail on a planted credential) |

## Critical-test coverage map

| # | Test | Status |
|---|---|---|
| 1 | Registration/login | Phase 2 |
| 2–4 | CV upload, extraction, verification | Phase 3 |
| 5–6 | Discovery, duplicate detection | Phase 5 (unique `(source, external_id)`) |
| 7–8 | Analysis, match score | Phase 6 |
| 9–10 | CV / cover-letter generation | Phase 7 |
| 11–14 | Job approval, final approval, submission blocking, approval invalidation | Phase 8 |
| 15 | User data isolation | DB-level isolation tested now; row-level per-user isolation in Phase 2 |
| 16 | Event idempotency | Envelope + contract done; consumer idempotency in Phase 5 |
| 17 | Desktop security boundaries | ✅ `src-tauri/tests/security.rs` + secret scan |

## Bugs found and fixed during Phase 1

- **CORS preflight rejected real clients.** The API client sends `x-client-type`, but the gateway didn't allow it. Only the live E2E caught this; the integration test now sends the exact client headers.
- 500 responses lost their correlation ID, because Starlette's catch-all handler sits outside user middleware. Now handled inside the correlation middleware.
- `apiBaseUrlSchema` threw instead of failing validation on non-URLs: Zod 4 runs refinements after a failed check.
- `configure_logging` removed foreign root handlers. It now replaces only its own.

## Known issues / limitations

- **Unsigned desktop builds.** No code-signing certificates are available. macOS bundles have not been built (they need a Mac); CI is configured for `macos-latest`, but CI has not yet run because the repository isn't on GitHub.
- **Desktop E2E is not full WebDriver yet.** Covered by component tests, config security tests and a real launch check. `tauri-driver` E2E is planned for Phase 9.
- `tauri dev` (hot-reload mode) was not exercised; only the release build was launched.
- The project lives in **OneDrive**. `node_modules`, `.venv` and `target/` are large and sync poorly; consider excluding them from OneDrive or moving the repo to a non-synced folder.
- The gateway's readiness probes open a fresh AMQP connection per check (via identity-service). Fine at a 15 s polling interval; switch to a shared connection once services publish events.
- The production API host (`https://api.careerpilot.example`) is a placeholder in `tauri.production.conf.json`.
- Redis 8 is distributed under AGPL/SSPL/RSAL terms; for production, consider a managed Redis or Valkey (BSD) as a drop-in.

## Next implementation step (Phase 2: Identity)

1. `identity-service`: `POST /v1/auth/register`, `/login`, `/refresh`, `/logout`, `GET /v1/users/me`, with Argon2id hashing, short-lived JWT access tokens (asymmetric keys, held server-side only), and rotating refresh tokens stored as SHA-256 hashes in `sessions`.
2. Gateway: Redis rate limiting on auth routes; JWT verification middleware that injects the verified `user_id` downstream.
3. Clients: shared login/registration screens in `@careerpilot/ui` (React Hook Form + Zod); Desktop token storage in the OS keychain via a scoped Tauri plugin (extend the `security.rs` allow-list); Web session handling per ADR-011.
4. Tests: registration/login from both clients (Playwright + desktop), cross-user access denial, rate-limit behavior.
