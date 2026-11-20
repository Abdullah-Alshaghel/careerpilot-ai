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
# 1. Configuration: generates .env with random local secrets and a token signing key
#    (existing .env from an earlier phase: python scripts/init-env.py --update)
python scripts/init-env.py

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

Both clients open on **sign in / create account**. Once signed in, **Career profile** is the main page: upload your CV (PDF, Word or text), and review every piece of information found in it (approve, reject, edit, or add your own). Nothing counts until you approve it. **Companies** keeps the companies you want to work for, with their careers pages and job sources (Greenhouse, Lever and Ashby boards are recognised from the URL). **Account** and **System status** are in the sidebar, with an English/Arabic (RTL) toggle. Desktop keeps the session in the OS keychain and Web in an HttpOnly cookie, so restarting or reloading keeps you signed in.

> **Windows note:** use `127.0.0.1` rather than `localhost` for command-line clients. On some machines `localhost` resolves to IPv6 `::1` first, and the connection stalls because the stack binds IPv4 loopback only. Browsers fall back automatically.

## Testing

```bash
# Python: unit + contract tests (from a venv with requirements-dev.txt installed)
py -3 -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check shared services tests scripts

# API/event contracts: regenerate after changing a service's response or event models
.venv/Scripts/python scripts/export-contracts.py

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
| Document service (direct, debug only) | http://127.0.0.1:8102 |
| Career profile service (direct, debug only) | http://127.0.0.1:8103 |
| Company service (direct, debug only) | http://127.0.0.1:8104 |
| PostgreSQL | 127.0.0.1:55432 |
| RabbitMQ management | http://127.0.0.1:15672 |
| S3 (SeaweedFS) | http://127.0.0.1:8333 |
| Grafana (`--profile observability`) | http://127.0.0.1:3001 |
