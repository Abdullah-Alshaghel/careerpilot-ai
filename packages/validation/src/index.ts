import { z } from "zod";
import type { ApiErrorBody, HealthCheckResult, HealthResponse } from "@careerpilot/contracts";

/**
 * Each schema is annotated with its contract type, so the compiler fails if
 * the Zod schema and the TypeScript contract drift apart.
 */

export const healthCheckResultSchema = z
  .object({
    name: z
      .string()
      .max(64)
      .regex(/^[a-z0-9-]+$/),
    status: z.enum(["up", "down"]),
    latency_ms: z.number().nonnegative(),
    detail: z.string().max(200).nullable().optional(),
  })
  .strict() satisfies z.ZodType<HealthCheckResult>;

export const healthResponseSchema = z
  .object({
    service: z.string().max(64),
    version: z.string().max(32),
    status: z.enum(["ok", "degraded", "down"]),
    timestamp: z.iso.datetime({ offset: true }),
    checks: z.array(healthCheckResultSchema),
  })
  .strict() satisfies z.ZodType<HealthResponse>;

export const apiErrorBodySchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    correlation_id: z.string().nullable(),
    fields: z.array(z.object({ loc: z.array(z.string()), msg: z.string() })).optional(),
  }),
}) satisfies z.ZodType<ApiErrorBody>;

/** Base URL for the API gateway — must be http(s) and have no path/query. */
export const apiBaseUrlSchema = z.url({ protocol: /^https?$/ }).refine((value) => {
  // Zod 4 still runs refinements after a failed .url() check, so parse defensively.
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  return (url.pathname === "/" || url.pathname === "") && !url.search && !url.hash;
}, "API base URL must not contain a path, query or fragment");
