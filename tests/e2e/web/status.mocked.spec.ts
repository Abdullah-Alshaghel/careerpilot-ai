import { expect, test } from "@playwright/test";
import { healthy, mockApi, openStatusPage } from "./fixtures";

test.describe("system status (mocked API, signed in)", () => {
  test("renders healthy platform status", async ({ page }) => {
    await mockApi(page, { signedIn: true });
    await openStatusPage(page);
    await expect(page.getByTestId("health-status")).toHaveAttribute("data-state", "ok");
    await expect(page.getByTestId("check-identity-service")).toContainText("Up");
    await expect(page.getByTestId("client-type")).toHaveText("Web app");
  });

  test("shows an actionable error when the backend is unreachable", async ({ page }) => {
    await mockApi(page, { signedIn: true });
    await page.route("**/v1/health", (route) => route.abort("connectionrefused"));
    await openStatusPage(page);
    await expect(page.getByTestId("health-status")).toHaveAttribute("data-state", "unreachable", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("health-error")).toContainText("Cannot reach the backend");
  });

  test("switches to Arabic and right-to-left layout, and remembers it", async ({ page }) => {
    await mockApi(page, { signedIn: true });
    await openStatusPage(page);
    await page.getByTestId("locale-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveAttribute("lang", "ar");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("حالة النظام");

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  });

  test("sends security headers", async ({ page }) => {
    await page.route("**/v1/health", (route) => route.fulfill({ json: healthy }));
    const response = await page.goto("/");
    const headers = response!.headers();
    expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["x-powered-by"]).toBeUndefined();
  });
});
