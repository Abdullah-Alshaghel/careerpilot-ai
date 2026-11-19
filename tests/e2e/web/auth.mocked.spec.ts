import { expect, test } from "@playwright/test";
import { errorBody, mockApi, session } from "./fixtures";

test.describe("sign-in and registration (mocked API)", () => {
  test("a visitor without a session sees the sign-in screen", async ({ page }) => {
    await mockApi(page, { signedIn: false });
    await page.goto("/");
    await expect(page.getByTestId("login-form")).toBeVisible();
    await expect(page.getByTestId("auth-connection")).toContainText("All systems operational");
  });

  test("signing in opens the workspace and the account page", async ({ page }) => {
    await mockApi(page, { signedIn: false });
    let sent: unknown;
    await page.route("**/api/session/login", async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({ json: session });
    });
    await page.goto("/");
    await page.getByLabel("Email").fill("ada@example.com");
    await page.getByLabel("Password").fill("correct horse battery");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByTestId("signed-in-user")).toContainText("ada@example.com");
    expect(sent).toEqual({ email: "ada@example.com", password: "correct horse battery" });

    const me = page.waitForRequest("**/v1/users/me");
    await page.getByTestId("nav-account").click();
    expect((await me).headers().authorization).toBe("Bearer access-e2e");
    await expect(page.getByTestId("account-email")).toHaveText("ada@example.com");
  });

  test("wrong credentials show a clear error", async ({ page }) => {
    await mockApi(page, { signedIn: false });
    await page.route("**/api/session/login", (route) =>
      route.fulfill({ status: 401, json: errorBody("invalid_credentials") }),
    );
    await page.goto("/");
    await page.getByLabel("Email").fill("ada@example.com");
    await page.getByLabel("Password").fill("wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("auth-error")).toHaveText("Email or password is incorrect");
  });

  test("registration validates the form before sending it", async ({ page }) => {
    await mockApi(page, { signedIn: false });
    let called = false;
    await page.route("**/api/session/register", (route) => {
      called = true;
      return route.fulfill({ status: 201, json: session });
    });
    await page.goto("/");
    await page.getByRole("button", { name: /Create an account/ }).click();
    await page.getByLabel("Email").fill("not-an-email");
    await page.getByLabel("Password", { exact: true }).fill("short");
    await page.getByLabel("Confirm password").fill("different");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByText("Enter a valid email address")).toBeVisible();
    await expect(page.getByText("Use at least 12 characters")).toBeVisible();
    await expect(page.getByText("Passwords do not match")).toBeVisible();
    expect(called).toBe(false);
  });

  test("a returning visitor is signed in automatically, and can sign out", async ({ page }) => {
    await mockApi(page, { signedIn: true });
    await page.goto("/");
    await expect(page.getByTestId("signed-in-user")).toContainText("ada@example.com");

    const logout = page.waitForRequest("**/api/session/logout");
    await page.getByTestId("sign-out").click();
    await logout;
    await expect(page.getByTestId("login-form")).toBeVisible();
  });

  test("the sign-in screen works in Arabic, right to left", async ({ page }) => {
    await mockApi(page, { signedIn: false });
    await page.goto("/");
    await page.getByTestId("locale-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("تسجيل الدخول");
    // Email and password inputs stay left-to-right inside the RTL layout.
    await expect(page.getByLabel("البريد الإلكتروني")).toHaveAttribute("dir", "ltr");
  });

  test("no tokens are written to web storage", async ({ page }) => {
    await mockApi(page, { signedIn: true });
    await page.goto("/");
    await expect(page.getByTestId("signed-in-user")).toBeVisible();
    const stored = await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    );
    expect(stored).not.toContain(session.access_token);
  });
});
