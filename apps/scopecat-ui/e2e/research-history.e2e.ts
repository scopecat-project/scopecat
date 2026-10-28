import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type TestInfo } from "@playwright/test";
import { ROOT, startProject, stopProject, uv } from "./daemon";

test("Chinese research management preserves identities, associations and conflict fencing", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-research-"));
  let started = false;
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const base = await startProject(project);
    started = true;
    const runId = uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import save_inputs
from ui_signal.signal import AMPLITUDE, FREQUENCY, signal
with project.connect() as lab:
    run = lab.run(signal.build().with_axis(sc.axis(FREQUENCY.ref, [sc.Quantity(4.8, "GHz")])).with_axis(sc.axis(AMPLITUDE.ref, [sc.Quantity(0.1, "V")])), config=save_inputs(lab))
    print(run.id)
`,
      project,
    ]);
    const sample = await page.request.post(`${base}/api/v1/samples`, {
      data: {
        operation_id: "research-sample",
        sample_id: "chip-user-id",
        kind: "synthetic",
        actor: "fixture",
        content: { display_name: "用户样品 Alpha" },
      },
    });
    expect(sample.status()).toBe(201);
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET" && new URL(request.url()).pathname.startsWith("/api/"))
        writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
    });
    await page.goto(`${base}/#history`);
    await page.getByText("研究项目管理", { exact: true }).click();
    await page.getByLabel("项目名称", { exact: true }).fill("用户项目 Alpha");
    await page.getByRole("button", { name: "创建项目", exact: true }).click();
    await expect(page.getByRole("option", { name: "用户项目 Alpha", exact: true })).toBeAttached();
    const id = await page.getByLabel("研究项目", { exact: true }).inputValue();
    expect(id).not.toBe("");
    await page.getByLabel("项目名称", { exact: true }).fill("用户项目 Beta");
    await page.getByRole("button", { name: "重命名项目", exact: true }).click();
    await expect(page.getByRole("option", { name: "用户项目 Beta", exact: true })).toBeAttached();
    await expect(page.getByLabel("研究项目", { exact: true })).toHaveValue(id);
    await page.getByLabel("样品", { exact: true }).selectOption("chip-user-id");
    await page.getByRole("button", { name: "关联样品", exact: true }).click();
    await expect(page.getByRole("button", { name: "移除样品关联", exact: true })).toBeVisible();
    await expect(page.getByText("没有符合筛选条件的 run。", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "移除样品关联", exact: true }).click();
    await expect(page.getByRole("button", { name: "移除样品关联", exact: true })).toHaveCount(0);
    await page.getByLabel("样品", { exact: true }).selectOption("");
    await page.getByLabel("显示项目外的 run 以供关联", { exact: true }).check();
    await page.getByRole("button", { name: "关联 run", exact: true }).click();
    await page.getByLabel("显示项目外的 run 以供关联", { exact: true }).uncheck();
    await expect(page.getByRole("button", { name: "移除 run 关联", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "移除 run 关联", exact: true }).click();
    await expect(page.getByText("没有符合筛选条件的 run。", { exact: true })).toBeVisible();
    expect((await page.request.get(`${base}/api/v1/runs/${runId}`)).status()).toBe(200);
    expect((await page.request.get(`${base}/api/v1/samples/chip-user-id`)).status()).toBe(200);
    const concurrent = await page.request.put(`${base}/api/v1/research-projects/${id}`, {
      data: {
        name: "另一会话的名称",
        description: "",
        expected_revision: 2,
      },
    });
    expect(concurrent.status()).toBe(200);
    await page.getByLabel("项目名称", { exact: true }).fill("过期编辑");
    const rejected = page.waitForResponse(
      (r) => r.url().endsWith(`/research-projects/${id}`) && r.request().method() === "PUT",
    );
    await page.getByRole("button", { name: "重命名项目", exact: true }).click();
    expect((await rejected).status()).toBe(409);
    await expect(page.getByRole("alert")).toContainText("项目更新冲突");
    await page.getByText("原始诊断", { exact: true }).click();
    await expect(page.getByLabel("项目名称", { exact: true })).toHaveValue("过期编辑");
    const retained = await page.request.get(`${base}/api/v1/research-projects`);
    expect(retained.status()).toBe(200);
    expect((await retained.json()).items.find((item: { id: string }) => item.id === id).name).toBe(
      "另一会话的名称",
    );
    expect(writes.filter((x) => x.startsWith("DELETE"))).toEqual([
      `DELETE /api/v1/research-projects/${id}/members/samples/chip-user-id`,
      `DELETE /api/v1/research-projects/${id}/members/runs/${runId}`,
    ]);
    await page.screenshot({ path: testInfo.outputPath("research-conflict.png"), fullPage: true });
    await testInfo.attach("Project association requests", {
      body: JSON.stringify(writes, null, 2),
      contentType: "application/json",
    });
    passed = true;
  } finally {
    if (started) await stopFixture(project, passed, testInfo);
    if (passed) await rm(project, { recursive: true, force: true });
  }
});

async function stopFixture(project: string, passed: boolean, testInfo: TestInfo) {
  try {
    stopProject(project);
  } catch (error) {
    await testInfo.attach("Daemon cleanup failure", {
      body: String(error),
      contentType: "text/plain",
    });
    // Preserve the original assertion failure; cleanup still fails an otherwise successful test.
    if (passed) throw error;
  }
}
