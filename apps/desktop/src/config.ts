import { apiBaseUrlSchema } from "@careerpilot/validation";

/** Public build-time configuration. Never place secrets in VITE_* variables. */
export const publicConfig = {
  apiBaseUrl: apiBaseUrlSchema.parse(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8080"),
} as const;
