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
