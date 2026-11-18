# Security

## Implemented (Phase 1)

| Control | Where | Verified by |
|---|---|---|
| No secrets in source; `.env` git-ignored, generated locally | `.env.example`, `.gitignore` | `git check-ignore`; CI generates throwaway secrets |
| Secrets typed `SecretStr` (no repr/log leakage) | service `settings.py` | `test_settings_repr_never_contains_secrets` |
| Log redaction (passwords, tokens, auth headers, CV text, URL credentials) | `careerpilot_common/logging.py` | `shared/python/tests/test_logging.py` |
| Access logs exclude query strings, headers and bodies | `correlation.py` | review + tests |
| Safe errors: no stack traces or upstream details to clients | `errors.py`, `correlation.py`, gateway proxy | `test_unhandled_error_is_generic…`, `test_proxy_upstream_down_is_502_without_details` |
| Correlation ID format validation (no log injection) | `correlation.py` | `test_malformed_correlation_id_is_replaced` |
| CORS explicit allow-list, wildcard rejected, no credentials | gateway | unit + live preflight integration test |
| Request body cap (413) | gateway proxy | `test_proxy_rejects_oversized_body…` |
| Per-service DB roles; PUBLIC connect revoked | `infrastructure/postgres/init` | `test_service_role_cannot_access_other_databases` |
| All host ports bound to 127.0.0.1 | `docker-compose.yml` | review |
| Non-root containers | Dockerfiles | review |
| Web: CSP, `frame-ancestors 'none'`, nosniff, no `x-powered-by` | `apps/web/next.config.ts` | Playwright `sends security headers` |
| Desktop: zero IPC permissions, no global Tauri object, strict CSP, frozen prototype, HTTPS-only production CSP | `src-tauri/*` | `src-tauri/tests/security.rs` |
| Client bundles contain no secrets | `scripts/check-bundle-secrets.mjs` | run in CI after builds |
| API responses validated at runtime (Zod) | `@careerpilot/api-client` | `invalid_response` tests |

## Rules for upcoming phases

- **Approval gates (Phase 8)** are enforced in the Approval/Application services, never in clients. Submission requires a valid, non-revoked final approval for the *current* `package_version`; any package change invalidates it.
- **Auth (Phase 2):** Argon2id password hashes; short-lived access tokens; refresh tokens stored only as SHA-256 hashes (`sessions.refresh_token_hash`); rotation and revocation. Desktop stores the refresh token in the OS keychain (scoped plugin); never in `localStorage`.
- **Rate limiting** in the gateway via Redis (Phase 2, login/register first).
- **Uploads (Phase 3):** type sniffing (not extension), size caps, no execution or rendering of uploaded content, private buckets, per-user object prefixes.
- **Prompt injection:** job descriptions and documents are untrusted data, passed to LLMs as delimited content. Agents have no tools that can approve or submit.
- **TLS** terminates at the edge in staging/production; the local stack is loopback-only HTTP.
