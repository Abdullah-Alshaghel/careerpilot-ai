import type { Page } from "@playwright/test";

export const user = {
  id: "6f1c2b9e-3d4a-4b8e-9a7c-2e5d1f0a9b31",
  email: "ada@example.com",
  locale: "en",
  status: "active",
  created_at: "2026-11-19T10:15:00Z",
};

export const session = {
  access_token: "access-e2e",
  token_type: "Bearer",
  expires_in: 600,
  user,
};

export const healthy = {
  service: "api-gateway",
  version: "0.1.0",
  status: "ok",
  timestamp: "2026-11-18T12:00:00Z",
  checks: [
    { name: "redis", status: "up", latency_ms: 1.2, detail: null },
    { name: "identity-service", status: "up", latency_ms: 6.3, detail: null },
  ],
};

export const errorBody = (code: string) => ({
  error: { code, message: code, correlation_id: "e2e-12345678" },
});

/** Mock the API for a visitor who is (or isn't) already signed in. */
export async function mockApi(page: Page, { signedIn }: { signedIn: boolean }) {
  await page.route("**/v1/health", (route) => route.fulfill({ json: healthy }));
  await page.route("**/v1/users/me", (route) => route.fulfill({ json: user }));
  await page.route("**/api/session/refresh", (route) =>
    signedIn
      ? route.fulfill({ json: session })
      : route.fulfill({ status: 401, json: errorBody("no_session") }),
  );
  await page.route("**/api/session/logout", (route) => route.fulfill({ status: 204 }));
  await page.route("**/v1/documents", (route) => route.fulfill({ json: { documents: [] } }));
  await page.route("**/v1/profile", (route) => route.fulfill({ json: emptyProfile }));
  await page.route("**/v1/companies", (route) => route.fulfill({ json: { companies: [] } }));
}

export const emptyProfile = {
  id: "b2000000-0000-4000-8000-000000000001",
  version: 1,
  updated_at: "2026-11-20T09:00:00Z",
  facts: [],
  conflicts: [],
};

/** Open the app signed in and go to System status (the profile page is the landing page). */
export async function openStatusPage(page: Page) {
  await page.goto("/");
  await page.getByTestId("nav-status").click();
}
