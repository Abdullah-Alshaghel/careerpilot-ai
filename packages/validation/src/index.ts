import { z } from "zod";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  type ApiErrorBody,
  type BrowserSession,
  type CareerDocument,
  type CareerFact,
  type CareerProfile,
  type Company,
  type CompanyList,
  type DocumentList,
  type FactType,
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

// --------------------------------------------------------------- documents

const isoDateTime = z.iso.datetime({ offset: true });

export const documentSchema = z
  .object({
    id: z.uuid(),
    filename: z.string().max(255),
    mime_type: z.enum([
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "text/plain",
    ]),
    size_bytes: z.int().nonnegative(),
    status: z.enum(["uploaded", "processing", "extracted", "failed"]),
    failure_reason: z.string().max(64).nullable(),
    created_at: isoDateTime,
    updated_at: isoDateTime,
  })
  .strict() satisfies z.ZodType<CareerDocument>;

export const documentListSchema = z
  .object({ documents: z.array(documentSchema) })
  .strict() satisfies z.ZodType<DocumentList>;

// ----------------------------------------------------------- career profile

export const factTypeSchema = z.enum([
  "personal",
  "summary",
  "employment",
  "education",
  "skill",
  "certification",
  "language",
]);

const partialDate = z.string().regex(/^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$/, "profile.error.date");
const optionalText = (max: number) =>
  z
    .string()
    .trim()
    .max(max)
    .transform((v) => (v === "" ? null : v))
    .nullable();
const optionalDate = z
  .string()
  .trim()
  .transform((v) => (v === "" ? null : v))
  .pipe(partialDate.nullable())
  .nullable();
const required = (max: number) => z.string().trim().min(1, "profile.error.required").max(max);

/**
 * Per-type value schemas. They mirror the Career Profile Service's rules and
 * are used by the fact editor; the server validates again on every write.
 */
export const factValueSchemas = {
  personal: z
    .object({
      field: z.enum(["full_name", "email", "phone", "location", "link"]),
      value: required(300),
    })
    .strict(),
  summary: z.object({ text: required(2000) }).strict(),
  employment: z
    .object({
      title: required(200),
      organization: optionalText(200),
      start: optionalDate,
      end: optionalDate,
      current: z.boolean(),
      description: optionalText(2000),
    })
    .strict(),
  education: z
    .object({
      degree: optionalText(200),
      institution: optionalText(200),
      start: optionalDate,
      end: optionalDate,
    })
    .strict()
    .refine((v) => Boolean(v.degree || v.institution), {
      message: "profile.error.degreeOrInstitution",
      path: ["degree"],
    }),
  skill: z.object({ name: required(80) }).strict(),
  certification: z
    .object({
      name: required(200),
      year: z.int().min(1950, "profile.error.year").max(2100, "profile.error.year").nullable(),
    })
    .strict(),
  language: z.object({ name: required(60), level: optionalText(60) }).strict(),
} as const satisfies Record<FactType, z.ZodType>;

export const careerFactSchema = z
  .object({
    id: z.uuid(),
    fact_type: factTypeSchema,
    value: z.record(z.string(), z.unknown()),
    source: z
      .object({
        document_id: z.uuid().nullable(),
        name: z.string().nullable(),
        excerpt: z.string().nullable(),
      })
      .strict()
      .nullable(),
    confidence: z.number().min(0).max(1),
    verification_status: z.enum(["extracted", "user_verified", "rejected"]),
    valid_from: z.iso.date().nullable(),
    valid_to: z.iso.date().nullable(),
    created_at: isoDateTime,
    updated_at: isoDateTime,
  })
  .strict() satisfies z.ZodType<CareerFact>;

export const careerProfileSchema = z
  .object({
    id: z.uuid(),
    version: z.int().positive(),
    updated_at: isoDateTime,
    facts: z.array(careerFactSchema),
    conflicts: z.array(
      z
        .object({
          kind: z.enum(["multiple_values", "different_dates", "different_details"]),
          fact_type: factTypeSchema,
          fact_ids: z.array(z.uuid()),
        })
        .strict(),
    ),
  })
  .strict() satisfies z.ZodType<CareerProfile>;

export const bulkUpdateResultSchema = z.object({ updated: z.int().nonnegative() }).strict();

// ---------------------------------------------------------------- companies

const connectorTypeSchema = z.enum(["careers_page", "greenhouse", "lever", "ashby"]);

export const companySchema = z
  .object({
    id: z.uuid(),
    name: z.string().max(200),
    careers_url: z.string().max(2048).nullable(),
    region: z.string().max(100).nullable(),
    priority: z.enum(["high", "medium", "low"]),
    notes: z.string().max(2000).nullable(),
    version: z.int().positive(),
    connectors: z.array(
      z
        .object({
          id: z.uuid(),
          connector_type: connectorTypeSchema,
          config: z.record(z.string(), z.string()),
          enabled: z.boolean(),
          created_at: isoDateTime,
          updated_at: isoDateTime,
        })
        .strict(),
    ),
    detected_connector: z
      .object({ connector_type: connectorTypeSchema, config: z.record(z.string(), z.string()) })
      .strict()
      .nullable(),
    created_at: isoDateTime,
    updated_at: isoDateTime,
  })
  .strict() satisfies z.ZodType<Company>;

export const companyListSchema = z
  .object({ companies: z.array(companySchema) })
  .strict() satisfies z.ZodType<CompanyList>;

/**
 * Company form. The server is the authority on URL safety (public https
 * host, no IPs or credentials) and explains rejections; this only catches
 * obvious mistakes early. Messages are i18n keys.
 */
export const companyFormSchema = z.object({
  name: z.string().trim().min(1, "company.error.nameRequired").max(200),
  careers_url: z
    .string()
    .trim()
    .max(2048)
    .refine((v) => v === "" || !/^[a-z][a-z0-9+.-]*:(?!\d)/i.test(v) || /^https:\/\//i.test(v), {
      message: "company.error.https_required",
    }),
  region: z.string().trim().max(100),
  priority: z.enum(["high", "medium", "low"]),
  notes: z.string().trim().max(2000),
});

export type CompanyForm = z.infer<typeof companyFormSchema>;

/** Public ATS identifiers: letters, digits, dot, dash, underscore. */
export const connectorSlugSchema = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/, "company.error.slug");
