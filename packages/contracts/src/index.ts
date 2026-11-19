/**
 * API contract types shared by the Desktop and Web clients.
 * Source of truth: shared/api-contracts/schemas/*.schema.json
 * Runtime validation lives in @careerpilot/validation.
 */

export type HealthCheckStatus = "up" | "down";
export type HealthStatus = "ok" | "degraded" | "down";

export interface HealthCheckResult {
  name: string;
  status: HealthCheckStatus;
  latency_ms: number;
  detail?: string | null;
}

/** GET /v1/health */
export interface HealthResponse {
  service: string;
  version: string;
  status: HealthStatus;
  /** ISO-8601 UTC timestamp */
  timestamp: string;
  checks: HealthCheckResult[];
}

/** Uniform error body returned by every backend service. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    correlation_id: string | null;
    fields?: { loc: string[]; msg: string }[];
  };
}

export const API_VERSION = "v1" as const;

// ---------------------------------------------------------------- identity

export type Locale = "en" | "ar";
export type UserStatus = "active" | "disabled";

/** GET /v1/users/me (shared/api-contracts/schemas/user.v1.schema.json) */
export interface User {
  id: string;
  email: string;
  locale: Locale;
  status: UserStatus;
  /** ISO-8601 UTC timestamp */
  created_at: string;
}

/** POST /v1/auth/register | /v1/auth/login | /v1/auth/refresh (auth-token.v1) */
export interface TokenResponse {
  access_token: string;
  token_type: "Bearer";
  /** Access-token lifetime, seconds */
  expires_in: number;
  /** Opaque, single-use; rotated on every refresh. Never stored by the Web client. */
  refresh_token: string;
  /** Seconds until the session ends (absolute; refresh does not extend it) */
  refresh_expires_in: number;
  user: User;
}

/** What a client keeps in memory: the token response minus the refresh token (auth-session.v1). */
export interface BrowserSession {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  user: User;
}

export interface RegisterRequest {
  email: string;
  password: string;
  locale?: Locale;
}

export interface LoginRequest {
  email: string;
  password: string;
}

/** Error codes the identity endpoints and the gateway's auth layer can return. */
export type AuthErrorCode =
  | "invalid_credentials"
  | "email_taken"
  | "account_disabled"
  | "invalid_refresh_token"
  | "refresh_token_superseded"
  | "refresh_token_reused"
  | "invalid_token"
  | "token_expired"
  | "unauthorized"
  | "rate_limited"
  | "auth_unavailable";

/** Password rules enforced by the Identity Service (clients mirror them for early feedback). */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
