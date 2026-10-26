import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { startProject, stopProject, uv } from "./daemon";
import {
  chooseAuthorContext,
  copyAuthorWorkspace,
  prepareAuthorContexts,
  reviewRetainedExperiment,
} from "./author-context";

test("frozen Preview A survives code and scan edits; late B cannot reopen its closed panel", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-frozen-preview-"));
  let started = false;
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST") writes.push(new URL(request.url()).pathname);
  });
  try {
    await copyAuthorWorkspace(project);
    const base = await startProject(project);
    started = true;
    prepareAuthorContexts(uv, project);
    await page.request.put(`${base}/api/v1/operator-settings`, {
      data: { operator: "Frozen preview tester", revision: 0 },
    });
    await page.goto(`${base}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    const preview = () =>
      page.waitForResponse(
        (response) =>
          response.url().endsWith("/experiment-launcher/preview") &&
          response.request().method() === "POST",
      );
    const aResponse = preview();
    await page.getByRole("button", { name: "预览", exact: true }).click();
    const a = await (await aResponse).json();
    await page.getByRole("button", { name: "保留并查看此次检查" }).click();
    const panel = page.getByRole("region", { name: "冻结检查 1", exact: true });
    await panel.getByText("冻结的配置、setup 与扫描输入", { exact: true }).click();
    const baseline = await panel.locator("pre").first().textContent();
    await expect(panel.getByText(a.code_revision.content_hash, { exact: true })).toBeVisible();
    await page.getByLabel("Frequency source").selectOption("range");
    await page.getByLabel("Frequency start").fill("4.7");
    await page.getByLabel("Frequency stop").fill("4.9");
    await page.getByLabel("Frequency points").fill("3");
    await page.getByLabel("实验配置", { exact: true }).selectOption("browser-bench-b");
    const sourcePath = join(project, "src/ui_author/signal.py");
    const source = await readFile(sourcePath, "utf8");
    await writeFile(sourcePath, source.replace("return gain /", "return 2 * gain /"));
    await page.getByRole("button", { name: "刷新作者代码", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "作者代码已刷新。" })).toBeVisible();
    await expect(panel.getByRole("status")).toContainText("与当前准备不同");
    await expect(panel.locator("pre").first()).toHaveText(baseline!);
    await expect(page.getByText("预览就绪", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "查看检查 1" }).click();
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeDisabled();
    await reviewRetainedExperiment(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let returned!: () => void;
    const workerReturned = new Promise<void>((resolve) => {
      returned = resolve;
    });
    await page.route("**/experiment-launcher/preview", async (route) => {
      const response = await route.fetch();
      returned();
      await gate;
      await route.fulfill({ response });
    });
    const bResponse = preview();
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await workerReturned;
    await page.getByRole("button", { name: "关闭检查视图" }).click();
    release();
    const b = await (await bResponse).json();
    await expect(page.getByText("预览就绪", { exact: true })).toBeVisible();
    await expect(panel).toHaveCount(0);
    expect(b.code_revision).not.toEqual(a.code_revision);
    expect(b.request_hash).not.toEqual(a.request_hash);
    expect(b.reviewed).not.toEqual(a.reviewed);
    expect(b.point_count).toBe(3);
    await page.getByRole("button", { name: "保留并查看此次检查" }).click();
    await expect(
      page
        .getByRole("region", { name: "冻结检查 2", exact: true })
        .getByText(b.code_revision.content_hash, { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "查看检查 1" }).click();
    await expect(panel.getByText(a.code_revision.content_hash, { exact: true })).toBeVisible();
    await expect(panel.locator("pre").first()).toHaveText(baseline!);
    // The old current-preview summary contains an unwrapped setup ID. Check the
    // frozen-only inspection after editing preparation, without widening that scope.
    await page.getByLabel("Gain", { exact: true }).fill("3");
    await expect(page.getByText("预览就绪", { exact: true })).toHaveCount(0);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await panel.scrollIntoViewIfNeeded();
      await expect
        .poll(() => page.evaluate<number>("document.documentElement.scrollWidth"))
        .toBeLessThanOrEqual(width);
      await page.screenshot({ path: testInfo.outputPath(`frozen-${width}.png`), fullPage: true });
    }
    expect(writes.filter((path) => path.endsWith("/experiment-launcher/preview"))).toHaveLength(2);
    const runs = await page.request.get(`${base}/api/v1/runs`);
    expect((await runs.json()).items).toHaveLength(0);
    const procedures = await page.request.get(`${base}/api/v1/procedures`);
    expect((await procedures.json()).items).toHaveLength(0);
    expect(writes.filter((path) => /\/(submit|stop|cancel)$/.test(path))).toEqual([]);
    // Each explicit Preview records its own fence; this is expected bookkeeping.
    const validity = await page.request.post(`${base}/api/v1/experiment-launcher/validity`, {
      data: a.manual_state,
    });
    expect(await validity.json()).toMatchObject({ valid: true, changes: [] });
  } finally {
    if (started) stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
