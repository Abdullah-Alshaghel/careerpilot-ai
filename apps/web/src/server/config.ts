import { apiBaseUrlSchema } from "@careerpilot/validation";

/**
 * Server-only configuration for the web app's route handlers. Read at request
 * time from the server environment; nothing here reaches the browser bundle.
 */
export function serverConfig() {
  return {
    // Gateway as reachable from this server. 127.0.0.1 avoids slow IPv6
    // fallback on Windows when the gateway binds IPv4 loopback only.
    gatewayUrl: apiBaseUrlSchema.parse(
      process.env.API_INTERNAL_BASE_URL ??
        process.env.NEXT_PUBLIC_API_BASE_URL ??
        "http://127.0.0.1:8080",
    ),
    secureCookies: process.env.NODE_ENV === "production",
  } as const;
}
