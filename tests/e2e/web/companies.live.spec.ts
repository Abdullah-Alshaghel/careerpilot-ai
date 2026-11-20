import { expect, test } from "@playwright/test";

test.skip(process.env.E2E_LIVE !== "1", "requires the docker compose backend (E2E_LIVE=1)");

const PASSWORD = "correct horse battery staple";

/** Phase 4 acceptance: full company CRUD from the browser against the real backend. */
test("creates, edits and deletes a company with its job sources", async ({ page, context }) => {
  test.setTimeout(60_000);
  const email = `e2e-co-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/");
  await page.getByRole("button", { name: /Create an account/ }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByTestId("signed-in-user")).toContainText(email, { timeout: 20_000 });

  await page.getByTestId("nav-companies").click();
  await expect(page.getByTestId("companies-empty")).toBeVisible();

  // Create: the server refuses an internal address, then accepts a public one.
  await page.getByRole("button", { name: "Add company" }).click();
  const form = page.getByTestId("company-form");
  await form.getByLabel("Company name").fill("Acme");
  await form.getByLabel("Careers page (https://)").fill("https://169.254.169.254/latest");
  await form.getByRole("button", { name: "Save" }).click();
  await expect(form).toContainText("Use the site's domain name, not an IP address");
  await form.getByLabel("Careers page (https://)").fill("https://jobs.lever.co/acme");
  await form.getByLabel("Region or city").fill("Riyadh");
  await form.getByRole("button", { name: "Save" }).click();

  const card = page.getByTestId("company-card");
  await expect(card.getByRole("heading", { name: "Acme" })).toBeVisible();
  await expect(card).toContainText("Medium priority");

  // The Lever board is recognised from the URL and added as a job source.
  await expect(card.getByTestId("detected-connector")).toContainText("Lever job board: acme");
  await card.getByRole("button", { name: "Add as job source" }).click();
  await expect(card.getByTestId("connector-row")).toContainText("Active");
  await card.getByRole("button", { name: "Pause" }).click();
  await expect(card.getByTestId("connector-row")).toContainText("Paused");

  // The careers page opens in a new tab (web).
  const popup = context.waitForEvent("page");
  await card.getByRole("button", { name: "Open careers page" }).click();
  expect((await popup).url()).toContain("jobs.lever.co/acme");

  // Edit, then reload: stored on the server.
  await card.getByRole("button", { name: "Edit: Acme" }).click();
  await card.getByLabel("Priority").selectOption("high");
  await card.getByRole("button", { name: "Save" }).click();
  await expect(card.getByTestId("company-form")).toHaveCount(0);
  await page.reload();
  await page.getByTestId("nav-companies").click();
  await expect(page.getByTestId("company-card")).toContainText("High priority");
  await expect(page.getByTestId("connector-row")).toContainText("Paused");

  // Delete.
  await page.getByRole("button", { name: "Delete: Acme" }).click();
  await page.getByRole("button", { name: "Yes, delete" }).click();
  await expect(page.getByTestId("companies-empty")).toBeVisible();
});
