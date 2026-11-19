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
| Desktop: only the 3 keychain commands are permitted, every command gated by the app manifest, no global Tauri object, strict CSP, frozen prototype, HTTPS-only production CSP | `src-tauri/*` | `src-tauri/tests/security.rs` |
| Client bundles contain no secrets | `scripts/check-bundle-secrets.mjs` | run in CI after builds |
| API responses validated at runtime (Zod) | `@careerpilot/api-client` | `invalid_response` tests |

## Implemented (Phase 2: identity)

| Control | Where | Verified by |
|---|---|---|
| Passwords hashed with Argon2id (RFC 9106 low-memory profile), rehashed on login if parameters change; 12 to 128 characters | `identity_service/security.py`, `schemas.py` | `test_security.py`, `test_register_returns_a_session_and_stores_only_hashes` |
| Unknown email and wrong password are indistinguishable (same message; a dummy hash is verified so timing matches) | `service.login` | `test_login_success_and_generic_failures` |
| Access tokens: EdDSA (Ed25519) JWT, 10 min, `typ=at+jwt`, `iss`/`aud`/`sid`/`jti` required; algorithm pinned (no `none` or HMAC confusion) | `careerpilot_common/auth.py` | `shared/python/tests/test_auth.py` |
| Signing key held only by Identity (`JWT_SIGNING_KEY`); everyone else verifies with the public JWKS, which the gateway does not expose | `identity_service/api.py`, gateway `edge.py` | `test_jwks_publishes_only_the_public_key`, `test_jwks_is_not_exposed_through_the_gateway` |
| Refresh tokens: 256-bit opaque, stored only as SHA-256, rotated on every use; absolute 30-day session | `service.refresh` | `test_refresh_rotates_the_token` |
| Refresh-token reuse detection: a rotated token presented after a 15 s grace window revokes the whole session; concurrent refreshes rotate exactly once (row lock) | `service._handle_unknown_refresh_token` | `test_reuse_after_grace_window_revokes_the_whole_session`, `test_concurrent_refreshes_rotate_exactly_once` |
| Logout revokes the session, and Identity rejects that session's access tokens immediately | `service.logout`, `service.active_user` | `test_logout_revokes_the_session_immediately_and_is_idempotent` |
| Gateway verifies tokens at the edge (401 before any service is called and before large bodies are buffered); services verify again themselves | gateway `edge.py`; services via `careerpilot_common.auth` | `test_protected_route_without_token_is_401_at_the_edge`, `test_me_with_forged_token_is_401` |
| Client-supplied identity headers (`X-Authenticated-User-Id`, `X-User-Id`) are stripped; spoofed `X-Forwarded-For` is ignored unless it comes from a trusted proxy | gateway `proxy.py`, `edge.client_ip` | `test_valid_token_is_forwarded_with_verified_identity_and_spoofed_headers_dropped`, `test_user_cannot_impersonate_another_user_via_headers` |
| Rate limits (Redis): login 20/min per IP, 5 failed logins per account per 15 min, register 10 per 10 min per IP, refresh 60/min per IP; `429` with `Retry-After`; keys hold hashes, never emails or IPs | gateway `ratelimit.py`, `edge.py` | `test_failed_logins_lock_the_account_not_the_ip`, `test_repeated_failed_logins_lock_the_account` (live) |
| User data isolation: `/v1/users/me` resolves only the token's own user; sign-out clears the client data cache | `service.active_user`, `@careerpilot/ui` providers | `test_me_returns_only_the_callers_own_account`, UI test "never shows the previous user's data" |
| Desktop: refresh token in the OS credential store (Windows Credential Manager / macOS Keychain) through 3 commands that accept only refresh-token-shaped values; access token in memory only | `src-tauri/src/session.rs`, `apps/desktop/src/keychain.ts` | `session::tests`, `security.rs`, desktop `App.test.tsx` |
| Web: refresh token in an `HttpOnly; Secure; SameSite=Strict; Path=/api/session` cookie set by the web server; the browser holds only the access token, in memory | `apps/web/src/server/session.ts` | `session.test.ts`, live E2E cookie assertions |
| Web session endpoints: same-origin `Origin` / `Sec-Fetch-Site` check plus JSON-only bodies (CSRF defence in depth) | `apps/web/src/server/session.ts` | "cross-site request protection" tests |
| Auth responses are `Cache-Control: no-store` | identity routes, web session endpoints | integration and unit tests |

### Known limitations (Phase 2)

- **Account enumeration at registration.** `409 email_taken` reveals that an address is registered (rate-limited to 10 per 10 min per IP). Removing it needs email verification, planned with the Notification Service.
- **Rate limiting fails open** when Redis is down (ADR-012). Argon2id cost still slows guessing.
- **Logout reaches other services within the access-token lifetime** (up to 10 min). Only Identity checks session revocation on every request.
- **Signing-key rotation:** verifiers support it (JWKS by `kid`, refetch on an unknown `kid`), but Identity publishes a single key. A rotation procedure (publish next key, switch, retire) is still to be written.
- **Windows Credential Manager** entries are readable by other processes running as the same Windows user. That is the platform's model; macOS Keychain items are bound to the app's code signature once builds are signed.
- **Web per-IP limits behind the web server:** the web app's server calls the gateway, so browsers share its address unless the gateway trusts it via `TRUSTED_PROXY_CIDRS` (with an edge proxy setting `X-Forwarded-For`). Per-account login limits apply regardless.
- No multi-factor authentication or password reset yet (both need the Notification Service for email).

## Rules for upcoming phases

- **Approval gates (Phase 8)** are enforced in the Approval/Application services, never in clients. Submission requires a valid, non-revoked final approval for the *current* `package_version`; any package change invalidates it.
- **Every new service verifies bearer tokens itself** with `careerpilot_common.auth.JwksCache` (fetching Identity's internal JWKS) and scopes every query by the verified `user_id`. The gateway's `X-Authenticated-User-Id` header is for logging and correlation, not authorization.
- **No tokens in web storage.** `localStorage` / `sessionStorage` hold UI preferences only.
- **Uploads (Phase 3):** type sniffing (not extension), size caps, no execution or rendering of uploaded content, private buckets, per-user object prefixes.
- **Prompt injection:** job descriptions and documents are untrusted data, passed to LLMs as delimited content. Agents have no tools that can approve or submit.
- **TLS** terminates at the edge in staging/production; the local stack is loopback-only HTTP.
