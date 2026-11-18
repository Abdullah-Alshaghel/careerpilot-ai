import { expect, test } from "@playwright/test";

test.skip(process.env.E2E_LIVE !== "1", "requires the docker compose backend (E2E_LIVE=1)");

test("web client connects to the real backend through the gateway", async ({ page }) => {
  const healthResponse = page.waitForResponse((r) => r.url().endsWith("/v1/health"));
  await page.goto("/");
  const response = await healthResponse;
  expect(response.status()).toBe(200);
  expect(response.headers()["x-correlation-id"]).toBeTruthy();

  await expect(page.getByTestId("health-status")).toHaveAttribute("data-state", "ok");
  await expect(page.getByTestId("check-redis")).toContainText("Up");
  await expect(page.getByTestId("check-identity-service")).toContainText("Up");
});
