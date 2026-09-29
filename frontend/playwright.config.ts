import { existsSync } from "node:fs";

import { defineConfig } from "@playwright/test";

// The compose.test stack arrives with P0-04. Until the file exists, and whenever
// E2E_BASE_URL points at a running app, Playwright starts no web server; the
// P0-02 harness spec needs none.
const COMPOSE_TEST = "../deploy/compose.test.yaml";
const startStack = !process.env.E2E_BASE_URL && existsSync(COMPOSE_TEST);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // A flaky test is a broken test (operating rule 12).
  retries: 0,
  reporter: [["list"], ["junit", { outputFile: "test-results/junit.xml" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "phone",
      use: {
        browserName: "chromium",
        viewport: { width: 375, height: 812 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 3,
      },
    },
    {
      name: "laptop",
      use: { browserName: "chromium", viewport: { width: 1280, height: 800 } },
    },
  ],
  ...(startStack && {
    webServer: {
      command: `docker compose -f ${COMPOSE_TEST} up --wait`,
      url: "http://localhost:8080/health/ready",
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
  }),
});
