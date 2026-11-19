# Architecture

Authoritative reference: `CareerPilot_AI_Technical_Architecture_v1.1_Desktop_Web.docx`
(v1.1 Desktop + Web addendum supersedes all mobile/Expo decisions in v1.0).

```
 Desktop (Tauri 2 + React)        Web (Next.js 16 + React)
          \                         /
           \   @careerpilot/* shared packages (ui, api-client, contracts, validation, i18n)
            \                     /
             HTTPS  ──►  API Gateway (FastAPI: routing, CORS, correlation IDs, body limits, health aggregation)
                               │
          Identity │ Career Profile │ Documents │ Companies │ Discovery │ Analysis │ Matching │ Applications │ Approval │ Notifications │ Analytics
                               │
                   Agent Orchestrator (LangGraph workers)
                               │
   PostgreSQL (one DB + role per service) │ RabbitMQ │ Redis │ S3-compatible storage (SeaweedFS locally)
```

## Repository layout

| Path | Contents |
|---|---|
| `apps/desktop` | Tauri 2 shell (`src-tauri`) + Vite/React frontend (`src`) |
| `apps/web` | Next.js App Router app (`src/app`) |
| `packages/contracts` | TS API types (mirror of `shared/api-contracts`) |
| `packages/validation` | Zod runtime schemas, compile-time checked against `contracts` |
| `packages/api-client` | Typed fetch client + TanStack Query hooks (`/react`) |
| `packages/ui` | Design tokens, components, app shell, shared screens |
| `packages/i18n` | en/ar dictionaries, RTL/LTR, formatting |
| `packages/config` | Shared tsconfig |
| `services/*` | FastAPI domain services (implemented: `api-gateway`, `identity-service`) |
| `apps/web/src/server` | Web server-only code: session endpoints that hold the refresh-token cookie (ADR-014) |
| `agents/*` | LangGraph agents (Phase 5+) |
| `shared/python` | `careerpilot_common`: config, JSON logging + redaction, correlation IDs, health, errors, event envelope, access-token verification. **No domain models.** |
| `shared/api-contracts`, `shared/event-schemas` | Language-neutral JSON Schemas + canonical examples |
| `infrastructure/*` | Postgres init, RabbitMQ config, SeaweedFS entrypoint, monitoring |
| `tests/contract`, `tests/integration`, `tests/e2e` | Cross-cutting test suites |

## Architecture decision records

Each records a reversible default chosen where the brief left a gap.

**ADR-001 — npm workspaces (not pnpm/Turborepo).** npm ships with Node; no extra tooling to install. Internal packages export TypeScript source (no build step); Vite and Next (`transpilePackages`) compile them. Revisit if CI times grow.

**ADR-002 — FastAPI gateway instead of Kong/Traefik (doc §3 lists either).** A ~150-line Python gateway is testable with pytest, shares logging/correlation/health code, and aggregates downstream health for clients. It contains no business logic: authorization is enforced by the owning service. Swap for Traefik/Kong behind the same routes if edge features (WAF, mTLS) are needed.

**ADR-003 — Services are created when their phase starts.** Phase 1 runs the gateway and identity service. Other `services/*` directories hold a README with ownership and phase. Each new service is built from the identity-service template. Matches doc §20: microservice-ready boundaries without deploying a dozen empty services.

**ADR-004 — One PostgreSQL instance, one database + login role per service.** `REVOKE ALL ... FROM PUBLIC` on each database plus `REVOKE CONNECT` on `postgres`/`template1`. Integration tests verify that a service role cannot connect to other databases. A separate instance per service can be adopted later without code changes (just `DATABASE_URL`).

**ADR-005 — Migrations run as a one-shot container (`identity-migrate`).** Prevents replicas racing on schema changes; services start only after `service_completed_successfully`.

**ADR-006 — SeaweedFS for local S3.** MinIO stopped publishing maintained community images in 2025; SeaweedFS (Apache-2.0) is actively maintained and S3-compatible. Production uses any S3 API (AWS S3, Hetzner Object Storage, etc.).

**ADR-007 — Python 3.12 in containers/CI, ≥3.10 supported locally.** The dev machine has 3.10; code avoids 3.11+ only syntax.

**ADR-008 — TypeScript 6.0.** TS 7 (native compiler) is out, but `typescript-eslint` supports `<6.1`. Upgrade when the lint toolchain supports TS 7.

**ADR-009 — Shared screens live in `@careerpilot/ui`.** Desktop and Web are thin hosts: config + `AppProviders` + the shared screen. Platform-specific behavior (native menus, OS keychain, deep links) is added per host without forking business logic, which stays on the backend.

**ADR-010 — Gateway buffers request bodies (12 MB cap) instead of streaming.** This bounds memory per request and rejects oversized uploads (413) before any service sees them. Revisit with presigned S3 uploads in Phase 3 for large documents.

**ADR-011 — Bearer tokens, no cookies, no credentialed CORS.** `credentials: "omit"` in the client and `allow_credentials=False` at the gateway remove the CSRF class of attacks. Token storage: Desktop → refresh token in the OS keychain through three scoped app commands (`src-tauri/src/session.rs`). This is a custom command rather than a third-party plugin, so the webview can touch exactly one keychain entry. Web → in-memory access token, with the refresh token in an HttpOnly cookie on the web origin only (ADR-014). Neither client persists access tokens.

**ADR-012 — Gateway rate limiting fails open.** Fixed windows in Redis (`INCR` + `EXPIRE NX`). If Redis is unreachable, requests are allowed and a warning is logged, so a cache outage does not become a sign-in outage. Argon2id cost and Identity's generic errors still slow guessing. Switch auth routes to fail-closed if threat modelling says otherwise.

**ADR-013 — Asymmetric access tokens, verified everywhere.** Only Identity holds an Ed25519 private key. It issues 10-minute `at+jwt` access tokens and opaque, rotating refresh tokens. The gateway and every service verify tokens with the public JWKS (`GET /.well-known/jwks.json` on Identity, internal only) using shared code in `careerpilot_common.auth`. The gateway rejects bad tokens at the edge and strips client-supplied identity headers; services verify again rather than trusting the network. EdDSA was chosen over RS256 for its small keys and signatures and its lack of parameter pitfalls.

**ADR-014 — Web sessions via the web app's own server.** `POST /api/session/{login,register,refresh,logout}` (Next.js route handlers) call the gateway, keep the refresh token in an `HttpOnly; Secure; SameSite=Strict; Path=/api/session` cookie and return only the access token. Page scripts, including any injected by XSS, cannot read the long-lived credential. When several tabs refresh at once, Identity allows a 15 s reuse grace and the web server answers `409 refresh_token_superseded` without touching the cookie, so the losing tab retries with the new one.

**ADR-015 — Readiness probes reuse one RabbitMQ connection.** Opening an AMQP connection per probe made readiness flap under load (found by the Phase 2 live E2E run). `careerpilot_common.health.RabbitMQProbe` keeps one connection and reopens it only after it closes.

## Service template (what every service has)

`create_service_app()` gives: JSON logs with secret redaction, `X-Correlation-ID` propagation (validated format), uniform error bodies with no internals leaked, `/health/live` and `/health/ready` (503 when a dependency is down), docs disabled in production, and optional OpenTelemetry tracing (`OTEL_EXPORTER_OTLP_ENDPOINT`). Each service adds its settings (env vars, secrets typed `SecretStr`), Dockerfile (non-root, healthcheck), Alembic migrations, and tests.
