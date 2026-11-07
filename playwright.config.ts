import { defineConfig } from "@playwright/test";

import { APP, BACKEND, VAULT } from "./e2e/servers";

// The tests run their own backend, worker and app on a temporary vault and on
// ports of their own, whatever the shell has, with stub models.
export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  // The tests share one vault, so they run one at a time.
  workers: 1,
  // On CI, a failed test keeps its trace and screenshot, which the workflow uploads.
  use: {
    baseURL: APP,
    trace: process.env.CI ? "retain-on-failure" : "off",
    screenshot: process.env.CI ? "only-on-failure" : "off",
  },
  webServer: [
    {
      command: "node e2e/start-backend.mjs",
      // Any answer means it's up: this route refuses a request with no token.
      url: `${BACKEND}/api/health`,
      ignoreHTTPSErrors: true,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `npx vite app --host 127.0.0.1 --port ${new URL(APP).port} --strictPort`,
      url: APP,
      env: { SECUREFOLIO_VAULT: VAULT },
    },
  ],
});
