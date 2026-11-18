# CareerPilot AI

AI-assisted career and job-application workspace, with a **Desktop** client (Tauri 2, Windows/macOS) and a **Web** client (Next.js), both served by one FastAPI microservice backend.

CareerPilot prepares applications. It never submits one without two explicit user approvals (job approval + final package approval), and these are enforced on the backend.

- Architecture & decisions: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Progress: [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)
- Security: [`docs/SECURITY.md`](docs/SECURITY.md) · Events: [`docs/EVENTS.md`](docs/EVENTS.md) · Desktop release: [`docs/DESKTOP_RELEASE.md`](docs/DESKTOP_RELEASE.md)

## Prerequisites

| Tool | Version used | Needed for |
|---|---|---|
| Docker Desktop | 29.x (Compose v5) | backend stack |
| Node.js | 24.x (≥22) | Desktop/Web/shared packages |
| Python | 3.10+ (3.12 in containers) | backend tests locally |
| Rust (rustup) | stable 1.100 | Desktop app |
| VS 2022 C++ Build Tools (Windows) / Xcode CLT (macOS) | n/a | Desktop app |

## Quick start

```bash
# 1. Configuration: copy and replace every change-me value (URL-safe characters)
cp .env.example .env

# 2. Backend: Postgres, Redis, RabbitMQ, S3 storage, migrations, identity-service, gateway
docker compose up -d --build --wait
curl http://127.0.0.1:8080/v1/health        # -> {"status":"ok",...}

# 3. JS dependencies
npm install

# 4a. Web: http://localhost:3000
npm run dev:web

# 4b. Desktop: opens a native window
npm run dev:desktop
```

Both clients show **System status**, which is live connectivity to the gateway and each backend dependency, with an English/Arabic (RTL) toggle.

> **Windows note:** use `127.0.0.1` rather than `localhost` for command-line clients. On some machines `localhost` resolves to IPv6 `::1` first, and the connection stalls because the stack binds IPv4 loopback only. Browsers fall back automatically.

## Testing

```bash
# Python: unit + contract tests (from a venv with requirements-dev.txt installed)
py -3 -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check shared services tests

# Python: integration tests against the running compose stack
CAREERPILOT_INTEGRATION=1 .venv/Scripts/python -m pytest tests/integration

# TypeScript
npm run lint && npm run typecheck && npm test

# Web E2E (Playwright); build first. E2E_LIVE=1 also runs specs against the real backend.
npx -w @careerpilot/e2e playwright install chromium
npm run build:web && E2E_LIVE=1 npm run test:e2e

# Desktop security-boundary tests (Rust)
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml

# Built bundles contain no secrets
npm run build:web && npm run build -w @careerpilot/desktop && npm run check:secrets
```

## Building installers

```bash
cd apps/desktop
npx tauri build --bundles nsis,msi     # Windows → src-tauri/target/release/bundle/{nsis,msi}
npx tauri build --bundles app,dmg      # macOS (on a Mac)
```

Builds are currently **unsigned**. Signing requires certificates; see [`docs/DESKTOP_RELEASE.md`](docs/DESKTOP_RELEASE.md).

## Local endpoints

| Service | URL |
|---|---|
| API gateway | http://127.0.0.1:8080 (`/v1/health`, `/docs`) |
| Identity service (direct, debug only) | http://127.0.0.1:8101 |
| PostgreSQL | 127.0.0.1:55432 |
| RabbitMQ management | http://127.0.0.1:15672 |
| S3 (SeaweedFS) | http://127.0.0.1:8333 |
| Grafana (`--profile observability`) | http://127.0.0.1:3001 |
