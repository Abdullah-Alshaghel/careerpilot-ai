import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080";
const isDev = process.env.NODE_ENV !== "production";

// Strict CSP: the browser may only talk to our own origin and the API gateway.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  `connect-src 'self' ${apiBaseUrl}${isDev ? " ws: http://localhost:*" : ""}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  // Workspace packages ship TypeScript source.
  transpilePackages: [
    "@careerpilot/ui",
    "@careerpilot/api-client",
    "@careerpilot/contracts",
    "@careerpilot/validation",
    "@careerpilot/i18n",
  ],
  outputFileTracingRoot: fileURLToPath(new URL("../../", import.meta.url)),
  turbopack: { root: fileURLToPath(new URL("../../", import.meta.url)) },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
