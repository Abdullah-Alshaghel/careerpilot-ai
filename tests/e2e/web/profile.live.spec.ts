import { expect, test, type Page } from "@playwright/test";

test.skip(process.env.E2E_LIVE !== "1", "requires the docker compose backend (E2E_LIVE=1)");

const PASSWORD = "correct horse battery staple";

const CV = [
  "Grace Hopper",
  "grace@example.com | +1 202 555 0147",
  "",
  "Work Experience",
  "Senior Software Engineer, Remington Rand Inc.    Jun 2019 - Present",
  "- Built the first compiler",
  "",
  "Education",
  "Doctor of Philosophy (PhD) in Mathematics",
  "Yale University, 1930 - 1934",
  "",
  "Skills",
  "COBOL, FLOW-MATIC, Leadership",
].join("\n");

async function register(page: Page) {
  const email = `e2e-cv-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/");
  await page.getByRole("button", { name: /Create an account/ }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  // Argon2id hashing is deliberately slow; parallel test workers make it slower.
  await expect(page.getByTestId("signed-in-user")).toContainText(email, { timeout: 20_000 });
}

/**
 * Real browser → gateway → Document Service → S3 → extraction worker →
 * RabbitMQ → Career Profile worker → review UI.
 */
test("uploads a CV, reviews the suggestions, and keeps the decisions", async ({ page }) => {
  test.setTimeout(90_000);
  await register(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Career profile");

  const upload = page.waitForResponse(
    (r) => r.url().endsWith("/v1/documents") && r.request().method() === "POST",
  );
  await page.getByTestId("file-input").setInputFiles({
    name: "Grace CV.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(CV, "utf8"),
  });
  expect((await upload).status()).toBe(201);

  const row = page.getByTestId("document-row");
  await expect(row).toContainText("Grace CV.txt");
  await expect(row.getByTestId("document-status")).toHaveText("Read", { timeout: 45_000 });

  // Suggestions arrive from the profile worker.
  const experience = page.getByTestId("section-employment");
  await expect(experience).toContainText("Senior Software Engineer · Remington Rand Inc", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("section-skill")).toContainText("FLOW-MATIC");
  await expect(page.getByTestId("review-counts")).toContainText("to review · 0 approved");

  // The evidence behind a suggestion is one click away.
  await experience.getByRole("button", { name: "Show source text" }).click();
  await expect(experience.getByTestId("fact-excerpt")).toContainText("Jun 2019 - Present");

  // Review: reject one, approve the rest.
  await page.getByRole("button", { name: "Reject: Leadership" }).click();
  await expect(page.getByTestId("review-counts")).toContainText("1 rejected");
  await page.getByRole("button", { name: "Approve all remaining" }).click();
  await expect(page.getByTestId("review-counts")).toContainText("0 to review");

  // Decisions are stored on the server, not in the page.
  await page.reload();
  await expect(page.getByTestId("review-counts")).toContainText("0 to review");
  await expect(page.getByTestId("review-counts")).toContainText("1 rejected");
  await expect(page.locator('[data-testid="fact-row"][data-status="rejected"]')).toContainText(
    "Leadership",
  );

  // Hostile input: an executable disguised as a PDF is refused by content.
  await page.getByTestId("file-input").setInputFiles({
    name: "cv.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.concat([Buffer.from("MZ"), Buffer.alloc(200)]),
  });
  await expect(page.getByTestId("upload-message")).toContainText(
    "Upload a PDF, Word (.docx) or plain-text file",
  );

  // Deleting the document keeps the approved facts.
  await page.getByRole("button", { name: "Delete Grace CV.txt" }).click();
  await page.getByRole("button", { name: "Yes, delete" }).click();
  await expect(page.getByTestId("document-row")).toHaveCount(0);
  await expect(page.getByTestId("section-skill")).toContainText("COBOL");
});
