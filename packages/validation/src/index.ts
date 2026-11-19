import { z } from "zod";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  type ApiErrorBody,
  type BrowserSession,
  type HealthCheckResult,
  type HealthResponse,
  type TokenResponse,
  type User,
} from "@careerpilot/contracts";

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

// ---------------------------------------------------------------- identity

export const userSchema = z
  .object({
    id: z.uuid(),
    email: z.string().max(320),
    locale: z.enum(["en", "ar"]),
    status: z.enum(["active", "disabled"]),
    created_at: z.iso.datetime({ offset: true }),
  })
  .strict() satisfies z.ZodType<User>;

const accessToken = z.string().min(1).max(4096);

export const tokenResponseSchema = z
  .object({
    access_token: accessToken,
    token_type: z.literal("Bearer"),
    expires_in: z.int().nonnegative(),
    refresh_token: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
    refresh_expires_in: z.int().nonnegative(),
    user: userSchema,
  })
  .strict() satisfies z.ZodType<TokenResponse>;

export const browserSessionSchema = z
  .object({
    access_token: accessToken,
    token_type: z.literal("Bearer"),
    expires_in: z.int().nonnegative(),
    user: userSchema,
  })
  .strict() satisfies z.ZodType<BrowserSession>;

/**
 * Form schemas. Messages are i18n keys, translated by the UI.
 * The server re-validates everything; these only give early feedback.
 */
export const loginFormSchema = z.object({
  email: z.string().trim().min(1, "auth.error.emailRequired").max(320),
  password: z.string().min(1, "auth.error.passwordRequired").max(PASSWORD_MAX_LENGTH),
});

export const registerFormSchema = z
  .object({
    email: z
      .string()
      .trim()
      .min(1, "auth.error.emailRequired")
      .max(254)
      .pipe(z.email("auth.error.emailInvalid")),
    password: z
      .string()
      .min(PASSWORD_MIN_LENGTH, "auth.error.passwordTooShort")
      .max(PASSWORD_MAX_LENGTH, "auth.error.passwordTooLong")
      .refine((value) => value.trim().length > 0, "auth.error.passwordTooShort"),
    confirmPassword: z.string(),
  })
  .refine((form) => form.password === form.confirmPassword, {
    message: "auth.error.passwordMismatch",
    path: ["confirmPassword"],
  });

export type LoginForm = z.infer<typeof loginFormSchema>;
export type RegisterForm = z.infer<typeof registerFormSchema>;
