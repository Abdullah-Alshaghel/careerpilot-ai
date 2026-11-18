import { defineConfig, devices } from "@playwright/test";

const WEB_URL = process.env.E2E_WEB_URL ?? "http://localhost:3000";

/**
 * Web E2E. Two kinds of specs:
 *  - *.mocked.spec.ts — API mocked at the network layer; deterministic, always run.
 *  - *.live.spec.ts   — require the docker compose backend (E2E_LIVE=1).
 */
export default defineConfig({
  testDir: "./web",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-viewport", use: { ...devices["Pixel 7"] } },
  ],
  webServer: process.env.E2E_WEB_URL
    ? undefined
    : {
        command: "npm run start -w @careerpilot/web",
        cwd: "../..",
        url: WEB_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
