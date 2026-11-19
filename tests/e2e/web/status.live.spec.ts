import { expect, test } from "@playwright/test";

test.skip(process.env.E2E_LIVE !== "1", "requires the docker compose backend (E2E_LIVE=1)");

const PASSWORD = "correct horse battery staple";

/**
 * Real browser → web server (session cookie) → gateway → Identity Service →
 * PostgreSQL. One account per browser project, used through its whole life.
 */
test.describe.serial("web client against the real backend", () => {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;

  test("registers, reaches the gateway, and sees its own account", async ({ page, context }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /Create an account/ }).click();
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Confirm password").fill(PASSWORD);

    const register = page.waitForResponse((r) => r.url().endsWith("/api/session/register"));
    await page.getByRole("button", { name: "Create account" }).click();
    const response = await register;
    expect(response.status()).toBe(201);
    expect(await response.text()).not.toContain("refresh_token");

    // Status page: the gateway and Identity are up.
    await expect(page.getByTestId("health-status")).toHaveAttribute("data-state", "ok");
    await expect(page.getByTestId("check-identity-service")).toContainText("Up");

    // Account page: GET /v1/users/me through the gateway with the access token.
    const me = page.waitForResponse((r) => r.url().endsWith("/v1/users/me"));
    await page.getByTestId("nav-account").click();
    expect((await me).status()).toBe(200);
    await expect(page.getByTestId("account-email")).toHaveText(email);

    // The refresh token is in an HttpOnly cookie scoped to the session endpoints.
    const cookie = (await context.cookies()).find((c) => c.name === "cp_refresh");
    expect(cookie).toMatchObject({ httpOnly: true, sameSite: "Strict", path: "/api/session" });
    const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }));
    expect(storage).not.toContain(cookie!.value);
  });

  test("signs out, cannot resume, and signs back in", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("login-form")).toBeVisible(); // new context: no cookie

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("wrong password!!");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("auth-error")).toHaveText("Email or password is incorrect");

    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("signed-in-user")).toContainText(email);

    // Reload: the session resumes from the cookie (refresh through the real BFF).
    await page.reload();
    await expect(page.getByTestId("signed-in-user")).toContainText(email);

    await page.getByTestId("sign-out").click();
    await expect(page.getByTestId("login-form")).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("login-form")).toBeVisible();
  });
});
