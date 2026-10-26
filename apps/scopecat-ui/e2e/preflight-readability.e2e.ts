import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { components } from "../src/api-schema";
type LaunchPreview = components["schemas"]["LaunchPreview"];
import { startProject, stopProject, uv } from "./daemon";
import { chooseAuthorContext, copyAuthorWorkspace, prepareAuthorContexts } from "./author-context";

test("preflight tables scroll locally and preserve unknown, bounded and planned meaning", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-preflight-"));
  let started = false;
  try {
    await copyAuthorWorkspace(project);
    const base = await startProject(project);
    started = true;
    prepareAuthorContexts(uv, project);
    await page.request.put(`${base}/api/v1/operator-settings`, {
      data: { operator: "Preview tester", revision: 0 },
    });
    await page.goto(`${base}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    const response = page.waitForResponse(
      (r) => r.url().endsWith("/experiment-launcher/preview") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "预览", exact: true }).click();
    const original: LaunchPreview = await (await response).json();
    // Presentation edge cases use the real worker response as their base. No submission
    // is made with these intercepted responses; frozen identity/admission has its own journey.
    const displayed = structuredClone(original);
    const stage = displayed.preflight!.stages[0];
    stage.entity_ids = ["entity-" + "q".repeat(96)];
    stage.executions = { kind: "exact", value: 1, unit: "runs", basis: "Author exact basis" };
    stage.points_per_execution = {
      kind: "bounded",
      lower: 1,
      upper: 32,
      unit: "points",
      basis: "Author bounds",
    };
    stage.shots_per_point_per_entity = {
      kind: "unknown",
      unit: "shots",
      basis: "Author unknown basis",
    };
    stage.products = [
      {
        id: "product-" + "x".repeat(96),
        retention: "retained",
        dims: ["shot"],
        shape: [null],
        dtype: "float64",
        unit: "V",
      },
    ];
    stage.selected_points = 1;
    stage.planned_settings_truncated = true;
    stage.planned_settings = [
      {
        point_index: 0,
        proposal_fingerprint: "proposal",
        operation_index: 0,
        assignment_index: 0,
        operation_id: "setup",
        instrument_id: "instrument-" + "x".repeat(96),
        setting: {
          target: {
            kind: "interface",
            interface_id: "rf-output/v1",
            component_path: ["channels", "1"],
            property_id: "frequency",
          },
          value: { value: 5000000000, unit: "Hz" },
        },
      },
    ];
    await page.route("**/experiment-launcher/preview", (route) =>
      route.fulfill({ json: displayed }),
    );
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await page.getByText("估算与计划测量", { exact: true }).click();
    await expect(page.getByTitle("Author exact basis", { exact: true })).toContainText(
      "精确： 1 runs",
    );
    await expect(page.getByTitle("Author bounds", { exact: true })).toContainText(
      "有界： 1–32 points",
    );
    await expect(page.getByTitle("Author unknown basis", { exact: true })).toContainText(
      "未知（shots）",
    );
    await expect(page.getByText(/不是已观测或已确认的状态/)).toBeVisible();
    await expect(page.getByText(/其余设置已省略/)).toBeVisible();
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() => page.evaluate<number>("document.documentElement.scrollWidth"))
        .toBeLessThanOrEqual(width);
      for (const name of ["产物表格", "计划仪器设置表格"]) {
        const region = page.getByRole("region", { name, exact: true });
        await region.focus();
        await expect(region).toBeFocused();
        const overflows = await region.evaluate((el) => el.scrollWidth > el.clientWidth);
        if (width === 390) expect(overflows).toBe(true);
        if (overflows) {
          await region.press("ArrowRight");
          await expect.poll(() => region.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
        }
        await region.screenshot({ path: testInfo.outputPath(`${name}-${width}.png`) });
      }
      await expect(page.getByText("5000000000 Hz", { exact: true })).toBeAttached();
      await page.screenshot({
        path: testInfo.outputPath(`preflight-${width}.png`),
        fullPage: true,
      });
    }
    stage.selected_points = 0;
    stage.planned_settings = [];
    stage.planned_settings_truncated = false;
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByText("未检查任何点；计划仪器设置未知。")).toBeAttached();
    await page.getByText("估算与计划测量", { exact: true }).click();
    await expect(page.getByText("未检查任何点；计划仪器设置未知。")).toBeVisible();
    delete displayed.preflight;
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByText(/未提供详细预检信息/)).toBeVisible();
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() => page.evaluate<number>("document.documentElement.scrollWidth"))
        .toBeLessThanOrEqual(width);
    }
    expect((await (await page.request.get(`${base}/api/v1/runs`)).json()).items).toHaveLength(0);
  } finally {
    if (started) stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
