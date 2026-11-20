import { expect, test } from "@playwright/test";

test.skip(process.env.E2E_LIVE !== "1", "requires the docker compose backend (E2E_LIVE=1)");

const PASSWORD = "correct horse battery staple";

/**
 * Jobs screen against the real backend. Deliberately stays off the internet:
 * it checks the guidance without sources and that importing a link to an
 * internal address is refused by the real fetcher.
 */
test("guides a new user and refuses internal addresses", async ({ page }) => {
  test.setTimeout(60_000);
  const email = `e2e-jobs-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/");
  await page.getByRole("button", { name: /Create an account/ }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByTestId("signed-in-user")).toContainText(email, { timeout: 20_000 });

  await page.getByTestId("nav-jobs").click();
  await expect(page.getByTestId("jobs-empty")).toContainText("Search your job sources");

  await page.getByRole("button", { name: "Search my job sources" }).click();
  await expect(page.getByTestId("search-error")).toContainText("Add a company with a job source");

  const form = page.getByTestId("import-form");
  await form
    .getByLabel("Job posting link (https://)")
    .fill("https://169.254.169.254/latest/meta-data/");
  await form.getByRole("button", { name: "Import" }).click();
  await expect(form.getByTestId("import-message")).toContainText("Refused for safety");
});
