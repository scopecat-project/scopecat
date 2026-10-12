import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type TestInfo } from "@playwright/test";
import type { components } from "../src/api-schema";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]): string {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 60_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(`${result.error?.message ?? ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout.trim();
}
async function stopProject(project: string, passed: boolean, testInfo: TestInfo) {
  try {
    uv(["scopecat", "stop", project]);
  } catch (error) {
    if (passed) throw error;
    await testInfo.attach("Cleanup failure", { body: String(error), contentType: "text/plain" });
  }
}
const SEED = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.slicing import signal, FREQUENCY, BIAS, REPEAT, MODE
from ui_signal.application import save_inputs
with project.connect() as lab:
    inputs = save_inputs(lab)
    plan = signal.build()
    for control, values in (
        (FREQUENCY.ref, [sc.Quantity(v, "GHz") for v in (4.6, 4.7, 4.9, 5.2)]),
        (BIAS.ref, [sc.Quantity(v, "V") for v in (-0.2, 0, 0.4)]),
        (REPEAT, [0, 1, 2]), (MODE, ["low", "high"]),
    ):
        plan = plan.with_axis(sc.axis(control, values))
    print(json.dumps(lab.run(plan, config=inputs).id))
`;

test("browses independent dimensions and keeps a compatible signal across slices", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(10_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-slicing-e2e-"));
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    await cp("e2e/fixtures/slicing-signal.py", join(project, "src/ui_signal/slicing.py"));
    const app = join(project, "src/ui_signal/application.py");
    const application = await readFile(app, "utf8");
    expect(application).toContain('author_modules=("ui_signal.signal",');
    await writeFile(
      app,
      application.replace(
        'author_modules=("ui_signal.signal",',
        'author_modules=("ui_signal.slicing", "ui_signal.signal",',
      ),
    );
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const runId = JSON.parse(uv(["python", "-c", SEED, project])) as string;
    const selections: { fixed_axis_indices: Record<string, number>; variable_ids: string[] }[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/measurements/query")) selections.push(request.postDataJSON());
    });
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(runId)}#runs`);
    await expect(page.getByLabel("Horizontal axis")).toBeVisible();
    const horizontal = page.getByLabel("Horizontal axis");
    const frequency = (await horizontal
      .locator("option")
      .filter({ hasText: "Frequency" })
      .getAttribute("value"))!;
    const bias = (await horizontal
      .locator("option")
      .filter({ hasText: "Bias" })
      .getAttribute("value"))!;
    const repeat = (await horizontal
      .locator("option")
      .filter({ hasText: "Repeat" })
      .getAttribute("value"))!;
    await page.getByLabel("Horizontal axis").selectOption(frequency);
    await page.getByLabel("Vertical axis").selectOption(bias);
    await expect(page.getByTestId("measurement-slice-summary")).toContainText("Mode = low");
    await expect(page.getByTestId("measurement-charts")).toBeVisible();
    const chart = page.getByLabel("Measurement chart", { exact: true });
    const phase = (await chart
      .locator("option")
      .filter({ hasText: "phase" })
      .getAttribute("value"))!;
    await chart.selectOption(phase);
    await page.getByRole("button", { name: "Next Repeat value", exact: true }).click();
    await expect(page.getByTestId("measurement-slice-summary")).toContainText("Repeat = 1");
    await expect(chart).toHaveValue(phase);
    await page.getByLabel("Mode slice", { exact: true }).selectOption("1");
    await expect(page.getByTestId("measurement-slice-summary")).toContainText("Mode = high");
    await expect(chart).toHaveValue(phase);
    await page.screenshot({
      path: testInfo.outputPath("multidimensional-heatmap.png"),
      fullPage: true,
    });
    await page.getByLabel("Vertical axis").selectOption("");
    await expect(page.getByLabel("Bias slice", { exact: true })).toBeVisible();
    await expect(chart).toHaveValue(phase);
    await expect
      .poll(() => Object.keys(selections.at(-1)?.fixed_axis_indices ?? {}).length)
      .toBe(3);
    await page.getByLabel("Horizontal axis").selectOption(repeat);
    await expect(page.getByLabel("Frequency slice", { exact: true })).toBeVisible();
    await expect(chart).toHaveValue(phase);
    await expect
      .poll(() => Object.hasOwn(selections.at(-1)?.fixed_axis_indices ?? {}, repeat))
      .toBe(false);
    await page.getByLabel("Horizontal axis").selectOption(frequency);
    await expect(page.getByLabel("Repeat slice", { exact: true })).toHaveValue("1");
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.getByRole("button", { name: "Focus chart", exact: true }).click();
    const focused = page.getByRole("dialog", { name: /measurements$/ });
    await expect(focused).toBeVisible();
    await expect(focused.getByRole("region", { name: "Run progress" })).toBeVisible();
    await expect(chart).toHaveValue(phase);
    await page.getByLabel("Repeat slice", { exact: true }).selectOption("2");
    await expect(page.getByTestId("measurement-slice-summary")).toContainText("Repeat = 2");
    for (const viewport of [
      { width: 1280, height: 720 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      const plot = focused.getByRole("img").first();
      await expect
        .poll(async () => {
          const rect = await plot.boundingBox();
          return (
            rect !== null &&
            rect.height >= 190 &&
            rect.y >= 0 &&
            rect.y + rect.height <= viewport.height &&
            rect.x + rect.width <= viewport.width
          );
        })
        .toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`chart-focus-${viewport.width}.png`) });
    }
    // Presentation-only fault injection over the real retained run. No hardware state is changed.
    const runUrl = `${endpoint.base_url}/api/v1/runs/${runId}`;
    const retained = (await (
      await page.request.get(runUrl)
    ).json()) as components["schemas"]["RunDetail"];
    let state: "running" | "attention_required" | "failed" = "running";
    await page.route(runUrl, (route) =>
      route.fulfill({
        json: {
          ...retained,
          control: {
            ...retained.control,
            state: state === "running" ? "leased" : state === "failed" ? "closed" : state,
            attention_reason:
              state === "attention_required" ? "Synthetic cleanup state is unknown" : null,
          },
          snapshot: {
            ...retained.snapshot,
            outcome:
              state === "failed" ? { ...retained.snapshot.outcome!, result: "failed" } : null,
          },
          resources:
            state === "running"
              ? []
              : [
                  {
                    resource: { id: "synthetic-resource", kind: "instrument" },
                    status: "quarantined",
                  },
                ],
        },
      }),
    );
    await page.route(`${runUrl}/failure-evidence`, (route) =>
      route.fulfill({
        json: {
          primary:
            state === "running"
              ? null
              : {
                  code: "synthetic_failure",
                  message: "Synthetic computation failed",
                  phase: "execution",
                  related_locations: [],
                },
          terminal_persistence: "unconfirmed",
          secondary: [],
          diagnostics: [],
          truncated: false,
        },
      }),
    );
    // Invoke the normal refresh handler as a fixture stimulus; the toolbar is inert in focus.
    const refreshState = () =>
      page.locator('[aria-label="Refresh project data"]').evaluate((button) => button.click());
    for (const problem of ["attention_required", "failed"] as const) {
      state = "running";
      await refreshState();
      await expect(focused.getByRole("region", { name: "Run progress" })).toContainText("Running");
      state = problem;
      await refreshState();
      await expect(
        focused.getByText("Synthetic computation failed", { exact: true }),
      ).toBeVisible();
      await expect(focused.getByText("Quarantined", { exact: true })).toBeVisible();
      await expect(focused.getByText(/Terminal persistence is unconfirmed/)).toBeVisible();
      await expect(focused.getByRole("button", { name: "Close without resuming" })).toHaveCount(0);
      if (problem === "attention_required") {
        await expect(
          focused.getByText("Synthetic cleanup state is unknown", { exact: true }),
        ).toBeVisible();
        await expect(focused.getByText(/Closing this record does not establish/)).toBeVisible();
      } else {
        await expect(focused.getByRole("region", { name: "Run progress" })).toContainText("Failed");
      }
      await page.screenshot({ path: testInfo.outputPath(`chart-focus-${problem}.png`) });
      if (problem === "attention_required") {
        await focused.getByRole("button", { name: "Exit focus to view run details" }).click();
        await expect(page.getByRole("button", { name: "Focus chart", exact: true })).toBeFocused();
        await expect(page.getByLabel("Repeat slice", { exact: true })).toHaveValue("2");
        await expect(page.getByRole("button", { name: "Close without resuming" })).toBeVisible();
        await page.getByRole("button", { name: "Focus chart", exact: true }).click();
      }
    }
    await page.keyboard.press("Escape");
    await expect(focused).not.toBeVisible();
    await expect(page.getByRole("button", { name: "Focus chart", exact: true })).toBeFocused();
    await expect(page.getByLabel("Repeat slice", { exact: true })).toHaveValue("2");
    await expect(chart).toHaveValue(phase);
    await expect(page.getByTestId("measurement-table")).not.toBeVisible();
    await page.locator("summary").filter({ hasText: "Record table" }).click();
    await expect(page.getByTestId("measurement-table")).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("multidimensional-curve.png"),
      fullPage: true,
    });
    for (const name of ["multidimensional-heatmap.png", "multidimensional-curve.png"])
      await testInfo.attach(name, { path: testInfo.outputPath(name), contentType: "image/png" });
    passed = true;
  } finally {
    await stopProject(project, passed, testInfo);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved synthetic project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
