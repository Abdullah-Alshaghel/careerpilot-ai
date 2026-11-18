import { apiBaseUrlSchema } from "@careerpilot/validation";

/**
 * Public, build-time configuration. Only NEXT_PUBLIC_* values reach the
 * browser bundle — never put secrets here.
 */
export const publicConfig = {
  apiBaseUrl: apiBaseUrlSchema.parse(
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080",
  ),
} as const;
