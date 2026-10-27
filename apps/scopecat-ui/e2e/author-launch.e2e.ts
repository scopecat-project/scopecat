import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import {
  chooseAuthorContext,
  copyAuthorWorkspace,
  prepareAuthorContexts,
  reviewRetainedExperiment,
} from "./author-context";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]): void {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 30_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
}

test("discovers an ordinary author experiment and edits controls before submitting", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-author-e2e-"));
  let completed = false;
  try {
    await copyAuthorWorkspace(project);
    uv([
      "python",
      "-m",
      "scopecat_server.cli",
      "start",
      project,
      "--port",
      "0",
      "--static-dir",
      resolve("dist"),
    ]);
    prepareAuthorContexts(uv, project);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    await page.goto(`${endpoint.base_url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Author launch tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    const sourceBounds = await page.getByLabel("代码工作区", { exact: true }).boundingBox();
    const refreshBounds = await page
      .getByRole("button", { name: "刷新作者代码", exact: true })
      .boundingBox();
    // Author refresh follows the source/draft/experiment row in the current workbench.
    expect(sourceBounds!.y + sourceBounds!.height).toBeLessThanOrEqual(refreshBounds!.y);
    await expect(page.getByRole("button", { name: "刷新作者代码", exact: true })).toBeInViewport();
    await testInfo.attach("Preparation source controls", {
      body: await page.screenshot(),
      contentType: "image/png",
    });
    await expect(page.getByLabel("Frequency", { exact: true })).toHaveValue("4.8");
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const source = page.getByLabel("Frequency 取值来源");
      await source.selectOption("fixed");
      const value = page.getByLabel("Frequency", { exact: true });
      await value.fill("4.9wat");
      await value.press("Tab");
      await expect(value).toHaveValue("4.9wat");
      await expect(page.getByRole("alert").filter({ hasText: "未知或不兼容的单位" })).toBeVisible();
      await value.fill("4900MHz");
      await value.press("Tab");
      await expect(value).toHaveValue("4.9");
      await page.getByLabel("Frequency 单位").selectOption("MHz");
      await expect(value).toHaveValue("4900");
      await source.selectOption("default");
      await expect(page.getByText("4.8 GHz · 声明默认值", { exact: true })).toBeVisible();
      await source.selectOption("values");
      const values = page.getByLabel("Frequency 扫描列表");
      await values.fill("4700MHz,4900MHz");
      await values.press("Tab");
      await expect(values).toHaveValue("4.7,4.9");
      await source.selectOption("range");
      await expect(page.getByLabel("Frequency 起点")).toHaveValue("4.8");
      await expect(page.getByLabel("Frequency 终点")).toHaveValue("");
      await page.getByLabel("Frequency 终点").fill("4.9");
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await page.screenshot({
        path: testInfo.outputPath(`control-inputs-${width}.png`),
        fullPage: true,
      });
    }
    await page.getByLabel("Gain", { exact: true }).fill("2");
    await page.getByLabel("Polarity", { exact: true }).selectOption("negative");
    await page.getByLabel("Frequency 取值来源").selectOption("range");
    await page.getByLabel("Frequency 起点").fill("4.7");
    await page.getByLabel("Frequency 终点").fill("4.9");
    await page.getByLabel("Frequency 点数").fill("3");
    const previewResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/preview") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "预览", exact: true }).click();
    const preview = await previewResponse;
    expect(preview.status()).toBe(200);
    expect(preview.request().postDataJSON().inputs).toEqual({ polarity: "negative" });
    const originalPreview = await preview.json();
    expect(originalPreview).toMatchObject({
      experiment_id: "signal",
      point_count: 3,
      controls: expect.arrayContaining([
        expect.objectContaining({ id: "frequency", state: "scanned" }),
      ]),
    });
    await expect(page.getByText("预览就绪", { exact: true })).toBeVisible();
    await page.getByRole("region", { name: "预览与开始", exact: true }).scrollIntoViewIfNeeded();
    await testInfo.attach("Preparation and launch actions", {
      body: await page.screenshot(),
      contentType: "image/png",
    });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.getByRole("heading", { name: "已解析控制项", exact: true })).toBeVisible();
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await page.screenshot({
        path: testInfo.outputPath(`control-summary-${width}.png`),
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    const sourcePath = join(project, "src/ui_author/signal.py");
    const originalSource = await readFile(sourcePath, "utf8");
    await writeFile(sourcePath, originalSource + "\ndef broken(:\n");
    await page.getByRole("button", { name: "刷新作者代码", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "SyntaxError" })).toBeVisible();
    await expect(page.getByText("预览就绪", { exact: true })).toBeVisible();
    await writeFile(sourcePath, originalSource.replace("return gain /", "return 2 * gain /"));
    await page.getByRole("button", { name: "刷新作者代码", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "作者代码已刷新。" })).toBeVisible();
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeDisabled();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("2");
    await expect(page.getByLabel("Frequency 点数")).toHaveValue("3");
    await reviewRetainedExperiment(page);
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeEnabled();
    const nextPreviewResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/preview") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "预览", exact: true }).click();
    const revisedPreview = await (await nextPreviewResponse).json();
    expect(revisedPreview.code_revision).not.toEqual(originalPreview.code_revision);
    expect(revisedPreview.point_count).toBe(3);
    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/submit") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "开始采集", exact: true }).click();
    const admitted = await submitted;
    expect(admitted.status()).toBe(200);
    expect(admitted.request().postDataJSON().inputs).toEqual({ polarity: "negative" });
    expect(await admitted.json()).toMatchObject({ dispatch_error: null });
    await expect(page.getByText("experiment: 已完成", { exact: true })).toBeVisible();
    const execution = page.getByRole("region", { name: "当前执行", exact: true });
    await expect(execution).toBeFocused();
    await expect(execution).toBeInViewport();
    await expect
      .poll(async () => {
        const heading = await execution.getByRole("heading", { name: "执行进度" }).boundingBox();
        const navigation = await page.getByRole("banner").boundingBox();
        return !!heading && !!navigation && heading.y >= navigation.y + navigation.height;
      })
      .toBe(true);
    await page.getByRole("button", { name: "准备", exact: true }).click();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("2");
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "监看", exact: true }).click();
    await expect(execution.getByText("执行诊断", { exact: true })).toBeVisible();
    await testInfo.attach("Selected monitor and retained editor input", {
      body: await page.screenshot(),
      contentType: "image/png",
    });
    await page.getByRole("link", { name: /^查看已保存结果:/ }).click();
    await expect(page.getByTestId("run-status")).toHaveText("已完成");
    await expect(page.getByText("Measurement data", { exact: true })).toBeVisible();
    completed = true;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved author project", {
        body: project,
        contentType: "text/plain",
      });
  }
});

test("prepares B while A stays pinned in a separate result page", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-author-ab-"));
  const release = join(project, "release-a");
  const submissions: string[] = [];
  context.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/experiment-launcher/submit"))
      submissions.push(request.postData() ?? "");
  });
  let completed = false;
  try {
    await copyAuthorWorkspace(project);
    const sourcePath = join(project, "src/ui_author/signal.py");
    const source = (await readFile(sourcePath, "utf8")).replace(
      "    detuning =",
      `    import time\n    from pathlib import Path\n    if gain == 2 and frequency.to("GHz").value > 4.7:\n        deadline = time.monotonic() + 90\n        while not Path(${JSON.stringify(release)}).exists():\n            if time.monotonic() > deadline:\n                raise RuntimeError("A was not released")\n            time.sleep(0.05)\n    detuning =`,
    );
    await writeFile(sourcePath, source);
    uv([
      "python",
      "-m",
      "scopecat_server.cli",
      "start",
      project,
      "--port",
      "0",
      "--static-dir",
      resolve("dist"),
    ]);
    prepareAuthorContexts(uv, project);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8"));
    await page.goto(`${endpoint.base_url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Author launch tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    await page.getByLabel("Gain", { exact: true }).fill("2");
    await page.getByLabel("Frequency 取值来源").selectOption("range");
    await page.getByLabel("Frequency 起点").fill("4.7");
    await page.getByLabel("Frequency 终点").fill("4.9");
    await page.getByLabel("Frequency 点数").fill("3");
    const preview = async () => {
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/experiment-launcher/preview") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: "预览", exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(200);
      return result.json();
    };
    const submit = async () => {
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/experiment-launcher/submit") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: "开始采集", exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(200);
      return result.json();
    };
    const previewA = await preview();
    const receiptA = await submit();
    const procedureA = new URL(page.url()).searchParams.get("procedure");
    const opened = context.waitForEvent("page");
    await page.getByRole("link", { name: "在新标签页或窗口打开结果" }).first().click();
    const resultA = await opened;
    await expect(resultA.getByTestId("run-status")).toHaveText("执行中");
    const runA = new URL(resultA.url()).searchParams.get("run");
    const readA = async () => {
      const response = await context.request.get(`${endpoint.base_url}/api/v1/runs/${runA}`);
      expect(response.ok()).toBe(true);
      return response.json();
    };
    const acceptedA = await readA();
    await page.setViewportSize({ width: 520, height: 800 });
    await page.getByRole("button", { name: "准备", exact: true }).click();
    await page.getByLabel("Gain", { exact: true }).fill("3");
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeDisabled();
    await writeFile(sourcePath, source.replace("return gain /", "return 2 * gain /"));
    await page.getByRole("button", { name: "刷新作者代码", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "作者代码已刷新。" })).toBeVisible();
    await page.getByRole("button", { name: "导航 / Open navigation", exact: true }).click();
    await page.getByRole("button", { name: "帮助 / Help", exact: true }).click();
    await page.goBack();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await page.goForward();
    await page.getByRole("button", { name: "导航 / Open navigation", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "实验 / Experiments", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "工作区导航", exact: true })).toHaveCount(0);
    await page.goBack();
    await reviewRetainedExperiment(page);
    await page.getByRole("button", { name: "准备新的 run（独立采集）", exact: true }).click();
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeEnabled();
    expect(submissions).toHaveLength(1);
    const previewB = await preview();
    expect(previewB.code_revision).not.toEqual(previewA.code_revision);
    await expect(resultA.getByTestId("run-status")).toHaveText("执行中");
    const receiptB = await submit();
    expect(receiptB).not.toEqual(receiptA);
    const procedureB = receiptB.procedure_id as string;
    expect(procedureB).not.toBe(procedureA);
    // Submission B must not silently rebind the already selected monitor A.
    expect(new URL(page.url()).searchParams.get("procedure")).toBe(procedureA);
    await page.getByRole("button", { name: "查看已提交任务", exact: true }).click();
    await expect(page).toHaveURL((url) => url.searchParams.get("procedure") === procedureB);
    await writeFile(release, "");
    await expect(resultA.getByTestId("run-status")).toHaveText("已完成");
    await expect(resultA.getByTestId("data-card").getByText(/^3 records/)).toBeVisible();
    expect(new URL(resultA.url()).searchParams.get("run")).toBe(runA);
    const finishedA = await readA();
    expect(finishedA.control.admission).toEqual(acceptedA.control.admission);
    expect(finishedA.snapshot.config_source).toEqual(acceptedA.snapshot.config_source);
    expect(finishedA.snapshot.scientific_binding).toEqual(acceptedA.snapshot.scientific_binding);
    await expect(page.getByText("experiment: 已完成", { exact: true })).toBeVisible();
    await testInfo.attach("A-B identities", {
      body: JSON.stringify(
        { previewA, previewB, receiptA, receiptB, runA, procedureA, procedureB },
        null,
        2,
      ),
      contentType: "application/json",
    });
    // A historical selection must survive an ordinary page round trip even after B was admitted.
    await page.getByRole("button", { name: "已提交任务", exact: true }).click();
    const historicalA = page
      .locator("details")
      .filter({ has: page.locator("summary").getByText("已提交任务", { exact: true }) })
      .getByRole("button")
      .last();
    await historicalA.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("link", { name: "重新打开此任务", exact: true })).toHaveAttribute(
      "href",
      `?procedure=${procedureA}#launch`,
    );
    await page.getByRole("button", { name: "导航 / Open navigation", exact: true }).click();
    await page.getByRole("button", { name: "帮助 / Help", exact: true }).click();
    await page.goBack();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await expect(page.getByRole("link", { name: "重新打开此任务", exact: true })).toHaveAttribute(
      "href",
      `?procedure=${procedureA}#launch`,
    );
    await page.goBack();
    await expect(page.getByRole("button", { name: "已提交任务", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.goBack();
    await expect(page.getByRole("link", { name: "重新打开此任务", exact: true })).toHaveAttribute(
      "href",
      `?procedure=${procedureB}#launch`,
    );
    await page.goForward();
    await expect(page.getByRole("button", { name: "已提交任务", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.goForward();
    await expect(page.getByRole("link", { name: "重新打开此任务", exact: true })).toHaveAttribute(
      "href",
      `?procedure=${procedureA}#launch`,
    );
    expect(submissions).toHaveLength(2);
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await expect(resultA.getByTestId("run-detail-header")).toContainText(runA!);
    completed = true;
  } finally {
    await writeFile(release, "");
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved author project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
