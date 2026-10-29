import { defineConfig, devices } from "@playwright/test";

// Rebalanced from CI run 40787744024: the original first project took 244s,
// while the second exhausted its 15-minute job before finishing 56 tests.
// Move ~505s of observed whole-file work to the first project; keep shared
// worker fixtures intact. All other (including new) files belong to the second.
const firstJourneyFiles = [
  "**/parameter-context.e2e.ts",
  "**/author-launch.e2e.ts",
  "**/manual-launch.e2e.ts",
  "**/candidate-adoption.e2e.ts",
  "**/candidate-launch.e2e.ts",
  "**/launch-recovery.e2e.ts",
  "**/manual-line-recovery.e2e.ts",
  "**/manual-line-scans.e2e.ts",
  "**/measurement-slicing.e2e.ts",
  "**/parallel-drafts.e2e.ts",
  "**/parameter-working-inputs.e2e.ts",
  "**/procedure-operator.e2e.ts",
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
