import {
  createDirectSessionTransport,
  type FetchLike,
  type RefreshTokenStore,
} from "@careerpilot/api-client";
import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { CareerPilotApp } from "./CareerPilotApp";
import { AppProviders } from "./providers";

export const ADA = {
  id: "6f1c2b9e-3d4a-4b8e-9a7c-2e5d1f0a9b31",
  email: "ada@example.com",
  locale: "en",
  status: "active",
  created_at: "2026-11-19T10:15:00Z",
};

export const healthyBody = {
  service: "api-gateway",
  version: "0.1.0",
  status: "ok",
  timestamp: "2026-11-18T12:00:00Z",
  checks: [
    { name: "redis", status: "up", latency_ms: 1.4, detail: null },
    { name: "identity-service", status: "up", latency_ms: 7.6, detail: null },
  ],
};

export const emptyProfile = {
  id: "b2000000-0000-4000-8000-000000000001",
  version: 1,
  updated_at: "2026-11-20T09:00:00Z",
  facts: [],
  conflicts: [],
};

export function tokensFor(user: typeof ADA, n = 1) {
  return {
    access_token: `access-${user.id}-${n}`,
    token_type: "Bearer",
    expires_in: 600,
    refresh_token: `${n}`.padEnd(43, "r"),
    refresh_expires_in: 2_592_000,
    user,
  };
}

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

export const apiError = (status: number, code: string, headers: Record<string, string> = {}) =>
  json({ error: { code, message: code, correlation_id: "srv-12345678" } }, status, headers);

export class MemoryStore implements RefreshTokenStore {
  constructor(public token: string | null = null) {}
  async load() {
    return this.token;
  }
  async save(token: string) {
    this.token = token;
  }
  async clear() {
    this.token = null;
  }
}

type Route = (body: unknown, init: RequestInit | undefined) => Response | Promise<Response>;

/** A fake gateway: map "METHOD /path" to handlers; health is healthy unless overridden. */
export function fakeGateway(routes: Record<string, Route>) {
  return vi.fn<FetchLike>(async (url, init) => {
    const path = new URL(url).pathname;
    const key = `${init?.method ?? "GET"} ${path}`;
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const handler = routes[key] ?? (key === "GET /v1/health" ? () => json(healthyBody) : null);
    if (!handler) return apiError(404, "not_found");
    return handler(body, init);
  });
}

export function renderApp(
  fetch: FetchLike,
  store = new MemoryStore(),
  clientType: "desktop" | "web" = "desktop",
  openExternal?: (url: string) => void,
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <AppProviders
      apiBaseUrl="http://localhost:8080"
      clientType={clientType}
      fetch={fetch}
      openExternal={openExternal}
      queryClient={queryClient}
      sessionTransport={(api) => createDirectSessionTransport(api, store)}
    >
      <CareerPilotApp />
    </AppProviders>,
  );
  return { ...view, store, queryClient };
}
