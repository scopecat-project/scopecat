import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type TestInfo } from "@playwright/test";
import type { components } from "../src/api-schema";
import { ROOT, uv, startProject, stopProject } from "./daemon";

async function cleanupProject(project: string, passed: boolean, testInfo: TestInfo) {
  try {
    stopProject(project);
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
    const baseUrl = await startProject(project);
    const runId = JSON.parse(uv(["python", "-c", SEED, project])) as string;
    const slices: {
      fixed: Record<string, number>;
      count: number;
      records: number;
    }[] = [];
    // Axis changes may abort obsolete queries after headers arrive; inspect only
    // completed bodies, which are also the responses available to the UI cache.
    page.on("requestfinished", async (request) => {
      if (!request.url().endsWith("/measurements/query")) return;
      const response = await request.response();
      if (!response?.ok()) return;
      const query = request.postDataJSON();
      const slice = (await response.json()) as components["schemas"]["MeasurementSlice"];
      slices.push({
        fixed: query.fixed_axis_indices,
        count: slice.selected_point_count,
        records: slice.items.length,
      });
    });
    const expectSlice = async (fixed: Record<string, number>, count: number) => {
      // Returning to a visited slice may reuse React Query data without another request.
      await expect.poll(() => slices).toContainEqual({ fixed, count, records: count });
    };
    await page.goto(`${baseUrl}/?run=${encodeURIComponent(runId)}#runs`);
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
    await expectSlice({ repeat: 0, mode: 0 }, 12);
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
    await expectSlice({ repeat: 1, mode: 1 }, 12);
    await expect(chart).toHaveValue(phase);
    await page.screenshot({
      path: testInfo.outputPath("multidimensional-heatmap.png"),
      fullPage: true,
    });
    await page.getByLabel("Vertical axis").selectOption("");
    await expect(page.getByLabel("Bias slice", { exact: true })).toBeVisible();
    await expectSlice({ bias: 0, repeat: 1, mode: 1 }, 4);
    await expect(chart).toHaveValue(phase);
    await page.getByLabel("Horizontal axis").selectOption(repeat);
    await expect(page.getByLabel("Frequency slice", { exact: true })).toBeVisible();
    await expectSlice({ frequency: 0, bias: 0, mode: 1 }, 3);
    await expect(chart).toHaveValue(phase);
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
      if (viewport.width === 390) {
        // Operate the controls at the narrow width, not just resize a desktop selection.
        for (const label of [
          "Horizontal axis",
          "Vertical axis",
          "Bias slice",
          "Repeat slice",
          "Mode slice",
          "Measurement chart",
        ]) {
          await expect(page.getByLabel(label, { exact: true })).toBeInViewport({ ratio: 1 });
        }
        await page.getByRole("button", { name: "Previous Repeat value", exact: true }).click();
        await expect(page.getByLabel("Repeat slice", { exact: true })).toHaveValue("1");
        await expect(chart).toHaveValue(phase);
        await page.getByRole("button", { name: "Next Repeat value", exact: true }).click();
        await expect(
          page.getByRole("button", { name: "Next Repeat value", exact: true }),
        ).toBeDisabled();
        await expect(page.getByLabel("Repeat slice", { exact: true })).toHaveValue("2");
        await expect(page.getByTestId("measurement-slice-summary")).toContainText("Repeat = 2");
        await expectSlice({ bias: 0, repeat: 2, mode: 1 }, 4);
        await chart.selectOption({ index: 0 });
        await chart.selectOption(phase);
        await expect(plot).toHaveAttribute("aria-label", /phase/);
      }
      await expect
        .poll(async () => {
          const rect = await plot.boundingBox();
          return (
            rect !== null &&
            rect.height >= 190 &&
            rect.y >= 0 &&
            rect.x >= 0 &&
            rect.y + rect.height <= viewport.height &&
            rect.x + rect.width <= viewport.width
          );
        })
        .toBe(true);
      const plotBounds = (await plot.boundingBox())!;
      const sourceBounds = (await page.getByTestId("plot-position").boundingBox())!;
      expect(sourceBounds.y).toBeGreaterThanOrEqual(plotBounds.y + plotBounds.height);
      await testInfo.attach(`chart-focus-${viewport.width}`, {
        body: await page.screenshot({
          path: testInfo.outputPath(`chart-focus-${viewport.width}.png`),
        }),
        contentType: "image/png",
      });
    }
    // Presentation-only fault injection over the real retained run. No hardware state is changed.
    const runUrl = `${baseUrl}/api/v1/runs/${runId}`;
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
    await cleanupProject(project, passed, testInfo);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved synthetic project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
