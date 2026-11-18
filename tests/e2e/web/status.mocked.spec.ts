import { expect, test } from "@playwright/test";

const healthy = {
  service: "api-gateway",
  version: "0.1.0",
  status: "ok",
  timestamp: "2026-11-18T12:00:00Z",
  checks: [
    { name: "redis", status: "up", latency_ms: 1.2, detail: null },
    { name: "identity-service", status: "up", latency_ms: 6.3, detail: null },
  ],
};

test.describe("system status (mocked API)", () => {
  test("renders healthy platform status", async ({ page }) => {
    await page.route("**/v1/health", (route) => route.fulfill({ json: healthy }));
    await page.goto("/");
    await expect(page.getByTestId("health-status")).toHaveAttribute("data-state", "ok");
    await expect(page.getByTestId("check-identity-service")).toContainText("Up");
    await expect(page.getByTestId("client-type")).toHaveText("Web app");
  });

  test("shows an actionable error when the backend is unreachable", async ({ page }) => {
    await page.route("**/v1/health", (route) => route.abort("connectionrefused"));
    await page.goto("/");
    await expect(page.getByTestId("health-status")).toHaveAttribute("data-state", "unreachable", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("health-error")).toContainText("Cannot reach the backend");
  });

  test("switches to Arabic and right-to-left layout, and remembers it", async ({ page }) => {
    await page.route("**/v1/health", (route) => route.fulfill({ json: healthy }));
    await page.goto("/");
    await page.getByTestId("locale-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveAttribute("lang", "ar");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("حالة النظام");

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  });

  test("sends security headers", async ({ page }) => {
    const response = await page.goto("/");
    const headers = response!.headers();
    expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["x-powered-by"]).toBeUndefined();
  });
});
