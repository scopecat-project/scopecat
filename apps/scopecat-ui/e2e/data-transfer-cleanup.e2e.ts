import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject, uv } from "./daemon";

const SEED = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
from ui_signal.analysis import PeakResult
with project.authoring() as author:
    content = initial_parameters()
    revision = author.parameters.save(name="transfer-inputs", catalog=content.catalog, parameters=content.parameters)
    author.use(parameters=revision.ref, setup=author.setup.get("initial"))
    run = author.prepare("signal", scans={"frequency": [4.7 + i * 0.002 for i in range(101)]}).run().wait().result()
    result = author.analyze_as(run.id, "ui_signal.analysis:estimate_peak", PeakResult)
    print(json.dumps({"run": run.id, "analysis": result.publication.id}))
`;

test("Chinese file transfer and same-operation cleanup preserve exact synthetic evidence", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-transfer-cleanup-"));
  let started = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const base = await startProject(project);
    started = true;
    const seed = JSON.parse(uv(["python", "-c", SEED, project])) as {
      run: string;
      analysis: string;
    };
    let original: Buffer | undefined;
    let copied: Buffer | undefined;
    let captureHash = "";
    let saves = 0;
    // Substitute host dialogs only. All file bytes and cleanup records use the real isolated daemon.
    await page.exposeFunction("exportSynthetic", async (runId: string) => {
      expect(runId).toBe(seed.run);
      const response = await page.request.get(`${base}/api/v1/data/runs/${runId}/file`);
      expect(response.status()).toBe(200);
      original = await response.body();
      return "/synthetic/export.scopecat";
    });
    await page.exposeFunction("openSynthetic", async () => {
      expect(original).toBeDefined();
      const response = await page.request.post(`${base}/api/v1/data/captures`, {
        data: original!,
        headers: { "content-type": "application/octet-stream" },
      });
      expect(response.status()).toBe(200);
      const receipt = await response.json();
      captureHash = receipt.capture.content_hash;
      return receipt;
    });
    await page.exposeFunction("saveSynthetic", async (hash: string) => {
      expect(hash).toBe(captureHash);
      saves++;
      if (saves === 2) return null;
      if (saves === 3) throw new Error("Synthetic destination is read-only");
      const response = await page.request.get(
        `${base}/api/v1/data/captures/${encodeURIComponent(hash)}/file`,
      );
      expect(response.status()).toBe(200);
      copied = await response.body();
      return "/synthetic/copy.scopecat";
    });
    await page.addInitScript(() => {
      const host = globalThis as unknown as Record<string, (...args: string[]) => Promise<unknown>>;
      Object.defineProperty(globalThis, "pywebview", {
        value: {
          api: {
            export_run: (id: string) => host.exportSynthetic(id),
            open_capture: () => host.openSynthetic(),
            save_capture: (hash: string) => host.saveSynthetic(hash),
            set_window_title: async () => {},
          },
        },
      });
    });
    const executionRequests: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        /\/(submit|analyze)$/.test(new URL(request.url()).pathname)
      )
        executionRequests.push(request.url());
    });
    await page.goto(`${base}/?run=${seed.run}#runs`);
    await page.getByRole("button", { name: "导出 Scopecat 文件…", exact: true }).click();
    await expect(
      page.getByText("已保存到 /synthetic/export.scopecat", { exact: true }),
    ).toBeVisible();
    await page.goto(`${base}/#history`);
    await page.getByText("导入数据", { exact: true }).click();
    await page.getByRole("button", { name: "打开 Scopecat 文件…", exact: true }).click();
    await expect(page.getByText("文件已导入。", { exact: true })).toBeVisible();
    const captured = page.getByRole("region", { name: "文件中的运行", exact: true });
    await expect(captured.getByText(`运行：${seed.run}`, { exact: true })).toBeVisible();
    await expect(
      captured.getByText("图表仅显示本页的 100 条记录。", { exact: true }),
    ).toBeVisible();
    await captured.getByRole("button", { name: "下一页测量记录" }).click();
    await expect(captured.getByText("图表仅显示本页的 1 条记录。", { exact: true })).toBeVisible();
    await page.goBack();
    await expect(
      captured.getByText("图表仅显示本页的 100 条记录。", { exact: true }),
    ).toBeVisible();
    await captured
      .getByRole("combobox", { name: "测量记录", exact: true })
      .selectOption("selected");
    await expect(captured.getByText(/个选定点，按逻辑点顺序排列/)).toBeVisible();
    await expect(captured.getByRole("region", { name: "保留的分析" })).toContainText(seed.analysis);
    const imported = page.getByRole("region", { name: "导入的结果", exact: true });
    const save = imported.getByRole("button", { name: "另存副本…", exact: true });
    await save.click();
    await expect(imported.getByText("已保存到 /synthetic/copy.scopecat")).toBeVisible();
    expect(copied!.equals(original!)).toBe(true);
    await save.click();
    await expect(save).toBeEnabled();
    await expect(imported.getByText("已保存到 /synthetic/copy.scopecat")).toHaveCount(0);
    await save.click();
    await expect(imported.getByRole("alert")).toContainText("Synthetic destination is read-only");
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(imported.getByRole("button", { name: "查看数据", exact: true })).toBeVisible();
      expect(
        await page.evaluate<boolean>("document.documentElement.scrollWidth <= innerWidth + 1"),
      ).toBe(true);
    }
    await imported.getByText("管理导入的结果", { exact: true }).click();
    await imported.getByRole("button", { name: "预览数据清理…" }).click();
    await expect(imported.getByText(`导入文件：${captureHash}`, { exact: true })).toBeVisible();
    await imported.getByRole("button", { name: "取消", exact: true }).click();
    expect((await (await page.request.get(`${base}/api/v1/data/captures`)).json()).length).toBe(1);
    await imported.getByRole("button", { name: "预览数据清理…" }).click();
    let operation = "";
    let showFailure = true;
    // Retain the real committed operation, inject only its displayed file-cleanup failure.
    await page.route("**/api/v1/data-cleanup", async (route) => {
      if (route.request().method() !== "POST") {
        const response = await route.fetch();
        const history = await response.json();
        await route.fulfill({
          response,
          json: history.map((item: { id: string }) =>
            item.id === operation && showFailure
              ? { ...item, state: "records_removed", error: "Synthetic file cleanup failure" }
              : item,
          ),
        });
        return;
      }
      const response = await route.fetch();
      const result = await response.json();
      expect(response.status()).toBe(200);
      operation = result.id;
      await route.fulfill({
        response,
        json: { ...result, state: "records_removed", error: "Synthetic file cleanup failure" },
      });
    });
    const remove = imported.getByRole("button", { name: "删除选定记录与文件", exact: true });
    await remove.focus();
    await page.keyboard.press("Enter");
    await expect(imported.getByText("暂无导入数据。")).toBeVisible();
    await page.getByText("数据清理", { exact: true }).click();
    await page.getByRole("button", { name: "数据清理历史", exact: true }).click();
    await expect(
      page.getByText("数据库记录已删除；物理文件清理尚未完成，需要重试。"),
    ).toBeVisible();
    const resumed = page.waitForRequest((request) =>
      request.url().endsWith(`/data-cleanup/${operation}/resume`),
    );
    showFailure = false;
    await page.getByRole("button", { name: "重试清理", exact: true }).click();
    await resumed;
    await expect(imported.getByText("暂无导入数据。")).toBeVisible();
    expect(original!.length).toBeGreaterThan(0);
    expect((await (await page.request.get(`${base}/api/v1/runs`)).json()).items).toHaveLength(1);
    expect(executionRequests).toEqual([]);
    await expect(page.getByText("记录与文件已清理。", { exact: true })).toBeVisible();
  } finally {
    if (started) stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
