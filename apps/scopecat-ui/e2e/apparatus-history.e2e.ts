import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { ROOT, startProject, stopProject, uv } from "./daemon";
import { expectRunIdentity } from "./run-identity";

// Every request made after fixture creation must be a named read. Resolve is a
// semantic read even though its exact subject reference travels in a POST body.
const readPaths = [
  /^\/api\/v1\/(health|events|events\/stream|procedures|operator-settings)$/,
  /^\/api\/v1\/apparatus-objects$/,
  /^\/api\/v1\/apparatus-objects\/[^/]+\/observations$/,
  /^\/api\/v1\/apparatus-observations\/[^/]+(?:\/attachments\/[^/]+)?$/,
  /^\/api\/v1\/(runs|record-collections|research-projects|samples|data\/captures|parameters\/branches|setup\/definitions|config|instruments|launch-attempts|parameters\/revisions|devices|author-workspaces|configuration-exchange\/imports|setup\/templates|analysis-publications)$/,
  /^\/api\/v1\/runs\/[^/]+(?:\/(contents|events|measurements|execution-segments|domain-decisions|analysis-actions|analysis-follows|request|failure-evidence|parameter-proposals|measured-costs|point-plan\/decisions|records\/compilation-cost\/json))?$/,
  /^\/api\/v1\/runs\/[^/]+\/measurements\/(preview|live)$/,
];
const readPosts = new Set(["/api/v1/apparatus-objects/resolve"]);

test("Data reads exact apparatus history, original downloads and run then returns", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-apparatus-"));
  let started = false;
  let base = "";
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    base = await startProject(project);
    started = true;
    const fixture = JSON.parse(
      uv(["python", resolve("e2e/fixtures/apparatus-history.py"), project]),
    );
    const audit: string[] = [];
    const rejected: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (!path.startsWith("/api/")) return;
      const action = `${request.method()} ${path}`;
      audit.push(action);
      if (
        !(request.method() === "GET" && readPaths.some((pattern) => pattern.test(path))) &&
        !(
          request.method() === "POST" &&
          (readPosts.has(path) || path === `/api/v1/runs/${fixture.runId}/measurements/query`)
        )
      )
        rejected.push(action);
    });
    await page.goto(`${base}/#history`);
    await page.getByRole("button", { name: "实验装置历史", exact: true }).click();
    await page.getByRole("textbox", { name: "查找实验装置对象" }).fill("blue");
    await page.getByRole("button", { name: "搜索对象", exact: true }).click();
    await expect(page.getByRole("button", { name: "Shared input line · line-a" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Shared input line · line-b" })).toBeVisible();
    await page.getByRole("button", { name: "Shared input line · line-a" }).click();
    await page.getByRole("button", { name: "Correction of original note · correction-a" }).click();
    const detail = page.getByRole("article", { name: "观察详情" });
    await expect(detail.getByText("Revised A description", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /查看本记录更正的原记录/ }).click();
    await expect(detail.getByText("Original A description", { exact: true })).toBeVisible();
    await expect(detail.getByText("2020-01-02T00:00:00Z", { exact: true })).toBeVisible();
    // The old record was not in the current 20-row page.
    await expect(
      page.getByRole("button", { name: "Original room-temperature note · original-a" }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "更早观察", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Original room-temperature note · original-a" }),
    ).toBeVisible();
    const attachment = detail.getByRole("link", { name: "下载原文件：same.pptx" });
    expect(audit.filter((entry) => entry.includes("/attachments/"))).toHaveLength(0);
    const [download] = await Promise.all([page.waitForEvent("download"), attachment.click()]);
    expect(await readFile(await download.path())).toEqual(
      Buffer.from("synthetic A original\x00\xff", "latin1"),
    );
    expect(download.suggestedFilename()).toBe("same.pptx");
    const response = await page.request.get(download.url());
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).toBe("application/octet-stream");
    expect(response.headers()["content-disposition"]).toMatch(/^attachment;/);
    expect(response.headers()["x-content-type-options"]).toBe("nosniff");
    const historyUrl = page.url();
    await detail
      .getByRole("button", { name: `打开 run：${fixture.runId}` })
      .scrollIntoViewIfNeeded();
    const scroll = await page.evaluate<number>("window.scrollY");
    await detail.getByRole("button", { name: `打开 run：${fixture.runId}` }).click();
    await expectRunIdentity(page, fixture.runId);
    await page.goBack();
    await expect(page).toHaveURL(historyUrl);
    await expect(detail.getByText("Original A description", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "查找实验装置对象" })).toHaveValue("blue");
    await expect.poll(() => page.evaluate<number>("window.scrollY")).toBeCloseTo(scroll, -1);
    await page.goForward();
    await expectRunIdentity(page, fixture.runId);
    await page.getByRole("button", { name: "数据 / Data", exact: true }).click();
    await expect(detail.getByText("Original A description", { exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("apparatus-before")).toBe(
      new URL(historyUrl).searchParams.get("apparatus-before"),
    );
    await page.getByRole("button", { name: "Shared input line · line-b" }).click();
    await page.getByRole("button", { name: "Independent B note · original-b" }).click();
    await expect(detail.getByText("Independent B description", { exact: true })).toBeVisible();
    const otherDownload = page.waitForEvent("download");
    await detail.getByRole("link", { name: "下载原文件：same.pptx" }).click();
    expect(await readFile(await (await otherDownload).path())).toEqual(
      Buffer.from("synthetic B original\x00\xfe", "latin1"),
    );
    const missing = new URL(historyUrl);
    missing.searchParams.set("apparatus-observation", "missing-observation");
    await page.goto(missing.href);
    await expect(page.getByRole("alert")).toContainText("观察不可用：missing-observation");
    await expect(page.getByRole("link", { name: /下载原文件/ })).toHaveCount(0);
    await page.goto(historyUrl);
    await expect(detail.getByText("Original A description", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("apparatus-history.png"), fullPage: true });
    await testInfo.attach("Read-only browser request audit", {
      body: JSON.stringify({ audit, rejected }, null, 2),
      contentType: "application/json",
    });
    expect(rejected).toEqual([]);
    passed = true;
  } finally {
    if (started) await stopFixture(project, base, page, testInfo);
    if (passed) await rm(project, { recursive: true, force: true });
  }
});

async function stopFixture(project: string, base: string, page: Page, testInfo: TestInfo) {
  try {
    stopProject(project);
  } catch (error) {
    // A container PID 1 may retain an exited daemon as a zombie. A failed
    // stop is acceptable only after verifying its HTTP service is gone.
    const alive = await page.request.get(`${base}/api/v1/health`, { timeout: 1000 }).then(
      () => true,
      () => false,
    );
    if (alive) throw error;
    await testInfo.attach("Daemon stop diagnostic (HTTP service exited)", {
      body: String(error),
      contentType: "text/plain",
    });
  }
}
