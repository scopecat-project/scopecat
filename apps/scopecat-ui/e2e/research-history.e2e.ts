import { spawn, type ChildProcess } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { ROOT, uv } from "./daemon";

test("Chinese research management preserves identities, associations and conflict fencing", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-research-"));
  let daemon: ChildProcess | undefined;
  let exited = false;
  let logs = "";
  let base = "";
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    // Own the foreground child so Node reaps it; detached start/stop leaves an
    // exited non-child with container PID 1, whose wait policy is outside this test.
    const python = uv(["python", "-c", "import sys; print(sys.executable)"]);
    daemon = spawn(
      python,
      [
        "-m",
        "scopecat_server.cli",
        "serve",
        project,
        "--port",
        "0",
        "--static-dir",
        resolve("dist"),
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    daemon.stdout!.on("data", (chunk) => {
      logs += String(chunk);
    });
    daemon.stderr!.on("data", (chunk) => {
      logs += String(chunk);
    });
    daemon.once("exit", () => {
      exited = true;
    });
    daemon.once("error", (error) => {
      logs += String(error);
      exited = true;
    });
    await expect
      .poll(
        async () => {
          if (exited) throw new Error(logs);
          try {
            base = JSON.parse(
              await readFile(join(project, ".scopecat/daemon.json"), "utf8"),
            ).base_url;
            return (await page.request.get(`${base}/api/v1/health`)).ok();
          } catch {
            return false;
          }
        },
        { timeout: 30_000 },
      )
      .toBe(true);
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
    if (daemon) await stopFixture(daemon, () => exited, base, page, passed, testInfo, logs);
    if (passed) await rm(project, { recursive: true, force: true });
  }
});

async function stopFixture(
  daemon: ChildProcess,
  exited: () => boolean,
  base: string,
  page: Page,
  passed: boolean,
  testInfo: TestInfo,
  logs: string,
) {
  try {
    daemon.kill("SIGTERM");
    await expect.poll(exited, { timeout: 10_000 }).toBe(true);
    expect(daemon.exitCode === 0 || daemon.signalCode === "SIGTERM").toBe(true);
    if (base) {
      const alive = await page.request.get(`${base}/api/v1/health`, { timeout: 1000 }).then(
        () => true,
        () => false,
      );
      expect(alive).toBe(false);
    }
  } catch (error) {
    await testInfo.attach("Daemon cleanup failure", {
      body: `${String(error)}\n${logs}`,
      contentType: "text/plain",
    });
    // Preserve the original assertion failure; cleanup still fails an otherwise successful test.
    if (passed) throw error;
  }
}
