import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
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
async function createProject() {
  const project = await mkdtemp(join(tmpdir(), "scopecat-slicing-e2e-"));
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
  return project;
}

async function expectContainedPlots(page: Page, count: number) {
  const plots = page.locator("figure").getByRole("img");
  await expect(plots).toHaveCount(count);
  for (const plot of await plots.all()) {
    await expect(plot.locator("canvas, svg").first()).toBeVisible();
    await expect
      .poll(async () => {
        const bounds = await plot.boundingBox();
        const frame = await plot.locator("xpath=ancestor::figure").boundingBox();
        return (
          bounds !== null &&
          frame !== null &&
          bounds.height >= 150 &&
          bounds.width > 0 &&
          bounds.x >= frame.x &&
          bounds.y >= frame.y &&
          bounds.x + bounds.width <= frame.x + frame.width &&
          bounds.y + bounds.height <= frame.y + frame.height
        );
      })
      .toBe(true);
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
  const project = await createProject();
  let passed = false;
  try {
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
    await expectContainedPlots(page, 1);
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
    await page.getByRole("button", { name: "聚焦曲线", exact: true }).click();
    const focused = page.getByRole("dialog", { name: /measurements$/ });
    await expect(focused).toBeVisible();
    await expect(focused.getByRole("region", { name: "run 进度" })).toBeVisible();
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
            cancellation_requested_at: state === "running" ? "2026-10-27T00:00:00Z" : null,
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
          secondary: [
            {
              code: "synthetic_cleanup_failed",
              message: "Synthetic cleanup failed",
              phase: "execution",
              related_locations: [],
            },
          ],
          diagnostics: [
            { generation: "a".repeat(32), retention: "unavailable_active_quota", href: null },
          ],
          truncated: true,
        },
      }),
    );
    // Invoke the normal refresh handler as a fixture stimulus; the toolbar is inert in focus.
    const refreshState = () =>
      page.locator('[aria-label="刷新项目数据"]').evaluate((button) => button.click());
    for (const problem of ["attention_required", "failed"] as const) {
      state = "running";
      await refreshState();
      await expect(focused.getByRole("region", { name: "run 进度" })).toContainText("执行中");
      await expect(focused.getByText(/取消尚未完成，也不表示设备已物理停止/)).toBeVisible();
      state = problem;
      await refreshState();
      await expect(
        focused.getByText("Synthetic computation failed", { exact: true }),
      ).toBeVisible();
      await expect(focused.getByText("Quarantined", { exact: true })).toBeVisible();
      await expect(focused.getByText(/尚未确认终态是否已持久保存/)).toBeVisible();
      await expect(focused.getByText(/诊断未保留/)).toBeVisible();
      await expect(focused.getByText("仅显示最近保存的部分证据。")).toBeVisible();
      const additional = focused.locator("summary").filter({ hasText: "其他失败证据（1）" });
      await additional.press("Enter");
      await expect(focused.getByText("Synthetic cleanup failed", { exact: true })).toBeVisible();
      await additional.press("Enter");
      await expect(focused.getByText(/取消尚未完成/)).toHaveCount(0);
      await expect(focused.getByRole("button", { name: "关闭且不继续" })).toHaveCount(0);
      if (problem === "attention_required") {
        await expect(
          focused.getByText("Synthetic cleanup state is unknown", { exact: true }),
        ).toBeVisible();
        await expect(focused.getByText(/关闭此记录不代表/)).toBeVisible();
      } else {
        await expect(focused.getByRole("region", { name: "run 进度" })).toContainText("失败");
      }
      await page.screenshot({ path: testInfo.outputPath(`chart-focus-${problem}.png`) });
      if (problem === "attention_required") {
        await focused.getByRole("button", { name: "退出聚焦，查看 run 详情" }).click();
        await expect(page.getByRole("button", { name: "聚焦曲线", exact: true })).toBeFocused();
        await expect(page.getByLabel("Repeat slice", { exact: true })).toHaveValue("2");
        await expect(page.getByRole("button", { name: "关闭且不继续" })).toBeVisible();
        await page.getByRole("button", { name: "聚焦曲线", exact: true }).click();
      }
    }
    await page.keyboard.press("Escape");
    await expect(focused).not.toBeVisible();
    await expect(page.getByRole("button", { name: "聚焦曲线", exact: true })).toBeFocused();
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

const LARGE_AXIS_SEED = `
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
        (FREQUENCY.ref, [sc.Quantity(v, "GHz") for v in (4.6, 5.2)]),
        (BIAS.ref, [sc.Quantity(0, "V")]),
        (REPEAT, list(range(257))), (MODE, ["low"]),
    ):
        plan = plan.with_axis(sc.axis(control, values))
    print(json.dumps(lab.run(plan, config=inputs).id))
`;

test("contains large-axis controls and multi-series plots in normal and focused views", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await createProject();
  let passed = false;
  try {
    const baseUrl = await startProject(project);
    const runId = JSON.parse(uv(["python", "-c", LARGE_AXIS_SEED, project])) as string;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${baseUrl}/?run=${encodeURIComponent(runId)}#runs`);
    await page.getByLabel("Vertical axis").selectOption("");
    const index = page.getByLabel("Repeat slice index", { exact: true });
    await expect(index).toHaveAttribute("max", "257");
    const column = index.locator("xpath=../..");
    await expectContainedPlots(page, 1);
    await testInfo.attach("large-axis-narrow", {
      body: await page.screenshot({
        path: testInfo.outputPath("large-axis-narrow.png"),
        fullPage: true,
      }),
      contentType: "image/png",
    });
    expect(await column.evaluate((element) => element.scrollWidth)).toBeLessThanOrEqual(
      await column.evaluate((element) => element.clientWidth),
    );
    await index.fill("257");
    await index.press("Enter");
    await expect(page.getByTestId("measurement-slice-summary")).toContainText("Repeat = 256");
    await expect(
      page.getByRole("button", { name: "Next Repeat value", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "Previous Repeat value", exact: true }).click();
    await expect(index).toHaveValue("256");
    await page.getByRole("button", { name: "聚焦曲线", exact: true }).click();
    await expect(index).toBeInViewport({ ratio: 1 });
    await index.fill("1");
    await index.press("Tab");
    await expect(
      page.getByRole("button", { name: "Previous Repeat value", exact: true }),
    ).toBeDisabled();
    expect(await column.evaluate((element) => element.scrollWidth)).toBeLessThanOrEqual(
      await column.evaluate((element) => element.clientWidth),
    );
    await page.keyboard.press("Escape");

    const traceRunId = JSON.parse(uv(["python", "-c", TRACE_SEED, project])) as string;
    await page.goto(`${baseUrl}/?run=${encodeURIComponent(traceRunId)}#runs`);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 844 });
      for (const focused of [false, true]) {
        if (focused) await page.getByRole("button", { name: "聚焦曲线", exact: true }).click();
        await expectContainedPlots(page, 1);
        await expect(page.locator("figure").getByRole("img")).toHaveAccessibleDescription(
          /2 data series/,
        );
        await testInfo.attach(`overlay-${width}-${focused}`, {
          body: await page
            .locator("figure")
            .screenshot({ path: testInfo.outputPath(`overlay-${width}-${focused}.png`) }),
          contentType: "image/png",
        });
        if (focused) await page.keyboard.press("Escape");
      }
    }
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

const TRACE_SEED = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.slicing import overlay, REPEAT
from ui_signal.application import save_inputs
with project.connect() as lab:
    inputs = save_inputs(lab)
    print(json.dumps(lab.run(overlay.build().with_axis(sc.axis(REPEAT, [0, 1])), config=inputs).id))
`;
