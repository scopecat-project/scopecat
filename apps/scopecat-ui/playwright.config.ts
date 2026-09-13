import { defineConfig, devices } from "@playwright/test";

// File costs from CI run 34759083211. Keep shared worker fixtures intact;
// every other (including newly added) file belongs to the second project.
const firstJourneyFiles = [
  "**/parameter-context.e2e.ts",
  "**/author-launch.e2e.ts",
  "**/manual-launch.e2e.ts",
];

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  projects: [
    { name: "journey-1", testMatch: firstJourneyFiles },
    { name: "journey-2", testIgnore: firstJourneyFiles },
  ],
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  outputDir: "test-results",
  use: {
    ...devices["Desktop Chrome"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
});
