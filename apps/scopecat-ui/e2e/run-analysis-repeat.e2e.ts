import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
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
const SEED = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
from ui_signal.analysis import PeakResult
with project.authoring() as author:
    content = initial_parameters()
    revision = author.parameters.save(name="repeat-inputs", catalog=content.catalog, parameters=content.parameters)
    author.parameters.create_branch("repeat-inputs", revision=revision)
    author.use(parameter_branch="repeat-inputs", setup=author.setup.get("initial"))
    run = author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}).run().wait().result()
    first = author.analyze_as(run.id, "ui_signal.analysis:estimate_peak", PeakResult)
    source = project.root / "src/ui_signal/analysis.py"
    source.write_text(source.read_text().replace("minimum_contrast: float = 0.2", "minimum_contrast: float = 2.0"))
    author.refresh()
    second = author.analyze_as(run.id, "ui_signal.analysis:estimate_peak", PeakResult, source="current")
    print(json.dumps({"run": run.id, "first": first.publication.id, "second": second.publication.id,
                     "source": first.publication.fact("author_code_revision").value}))
`;

test("repeats a retained ordinary analysis with its original source after author edits without acquisition", async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-analysis-repeat-"));
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
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
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const seed = JSON.parse(uv(["python", "-c", SEED, project])) as {
      run: string;
      first: string;
      second: string;
      source: string;
    };
    expect(seed.first).not.toBe(seed.second);
    const exported = await page.request.get(
      `${endpoint.base_url}/api/v1/data/runs/${seed.run}/file`,
    );
    expect(exported.status(), await exported.text()).toBe(200);
    const imported = await page.request.post(`${endpoint.base_url}/api/v1/data/captures`, {
      data: await exported.body(),
      headers: { "content-type": "application/octet-stream" },
    });
    expect(imported.status(), await imported.text()).toBe(200);
    let posts = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/author-revisions/analyze"))
        posts++;
    });
    await page.goto(
      `${endpoint.base_url}/?run=${encodeURIComponent(seed.run)}&run-analysis=${encodeURIComponent(seed.first)}#runs`,
    );
    // 在同一会话中从 Data 找回旧结果，独立窗口和返回不能丢失浏览条件。
    await page.getByRole("button", { name: /Data$/, exact: true }).click();
    const data = page.getByRole("region", { name: "数据", exact: true });
    await expect(data.getByRole("heading", { name: "Retained runs" })).toBeVisible();
    await data.getByLabel("起始时间（本地）").fill("2020-01-01T00:00");
    const importedResults = data.getByRole("region", { name: "导入的结果" });
    await expect(importedResults.getByText(`入口运行：${seed.run}`, { exact: true })).toBeVisible();
    await expect(importedResults.getByRole("button", { name: "预览数据清理…" })).toBeHidden();
    await importedResults.getByRole("button", { name: "查看数据", exact: true }).click();
    await expect(data.getByRole("region", { name: "文件中的运行" })).toBeVisible();
    await page.goBack();
    await expect(data.getByRole("region", { name: "文件中的运行" })).toBeHidden();
    await expect(data.getByLabel("起始时间（本地）")).toHaveValue("2020-01-01T00:00");
    await data.getByText("研究项目管理", { exact: true }).click();
    await data.getByLabel("项目名称", { exact: true }).fill("Retained comparison");
    await data.getByRole("button", { name: "Create project", exact: true }).click();
    await expect(data.getByLabel("研究项目", { exact: true })).not.toHaveValue("");
    await data.getByLabel("显示项目外的 run 以供关联").check();
    await data.getByRole("button", { name: "关联 run", exact: true }).click();
    await data.getByText("研究项目管理", { exact: true }).click();
    await expect(data.getByRole("button", { name: "关联 run", exact: true })).toBeHidden();
    await expect(data.getByText("Showing runs across all projects for association.")).toBeVisible();
    const selectedProject = await data.getByLabel("研究项目", { exact: true }).inputValue();
    const popupPromise = page.waitForEvent("popup");
    await data.getByRole("link", { name: "在新标签页或窗口打开结果" }).click();
    const popup = await popupPromise;
    await expect(popup).toHaveURL(new RegExp(encodeURIComponent(seed.run)));
    await popup.close();
    const row = data.locator("li").filter({
      has: page.locator(`a[href="/?run=${encodeURIComponent(seed.run)}"]`),
    });
    await expect(row).toHaveCount(1);
    await row.getByRole("button").first().click();
    await expect(page).toHaveURL(new RegExp(`\\?run=${encodeURIComponent(seed.run)}$`));
    await page.goBack();
    await expect(data.getByLabel("研究项目", { exact: true })).toHaveValue(selectedProject);
    await expect(data.getByLabel("起始时间（本地）")).toHaveValue("2020-01-01T00:00");
    await page.evaluate("window.scrollTo(0, 0)");
    await page.screenshot({ path: testInfo.outputPath("data-results.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate("window.scrollTo(0, 0)");
    await page.screenshot({ path: testInfo.outputPath("data-results-narrow.png"), fullPage: true });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(
      `${endpoint.base_url}/?run=${encodeURIComponent(seed.run)}&run-analysis=${encodeURIComponent(seed.first)}#runs`,
    );
    await expect(page.getByTestId("publication-id").first()).toHaveText(seed.first);
    const sources = page.getByText("来源与执行证据", { exact: true }).first();
    await expect(sources.locator("..")).not.toHaveAttribute("open");
    await sources.click();
    await expect(page.getByRole("heading", { name: "输入", exact: true }).first()).toBeVisible();
    await sources.click();
    await expect(page.getByTestId("publication-id").first()).toHaveText(seed.first);
    await page
      .getByTestId("publication-id")
      .first()
      .locator("xpath=ancestor::section[1]")
      .screenshot({ path: testInfo.outputPath("analysis-results.png") });
    const action = page.getByRole("region", { name: "重新执行已保存的分析" });
    await expect(action.getByRole("button", { name: "按已保存设置重新分析" })).toBeVisible();
    expect(posts).toBe(0);
    const pendingResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/author-revisions/analyze") &&
        response.request().method() === "POST",
    );
    await action.getByRole("button", { name: "按已保存设置重新分析" }).click();
    const result = await pendingResponse;
    expect(result.status(), await result.text()).toBe(200);
    expect(result.request().postDataJSON()).toMatchObject({
      run_id: seed.run,
      code_revision: { content_hash: seed.source },
      arguments: {},
    });
    const receipt = await result.json();
    await expect(action.getByRole("link", { name: "查看已保存的结果" })).toHaveAttribute(
      "href",
      `?run=${encodeURIComponent(seed.run)}&run-analysis=${encodeURIComponent(receipt.analysis_id)}#runs`,
    );
    await action.screenshot({ path: testInfo.outputPath("repeat-saved-analysis.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await action.screenshot({ path: testInfo.outputPath("repeat-saved-analysis-narrow.png") });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    const saved = await page.request.get(
      `${endpoint.base_url}/api/v1/runs/${seed.run}/analyses/${receipt.analysis_id}`,
    );
    const publication = (await saved.json()).analysis;
    expect(
      publication.outputs.find((output: { id: string }) => output.id === "result").content.value
        .status,
    ).toBe("estimated");
    expect(
      publication.outputs.find((output: { id: string }) => output.id === "author_code_revision")
        .content.value,
    ).toBe(seed.source);
    await action.getByRole("link", { name: "查看已保存的结果" }).click();
    await expect(page.getByTestId("publication-id").first()).toHaveText(receipt.analysis_id);
    await page.reload();
    await expect(page.getByRole("button", { name: "按已保存设置重新分析" }).first()).toBeVisible();
    expect(posts).toBe(1);
    const runs = await page.request.get(`${endpoint.base_url}/api/v1/runs?limit=100`);
    expect((await runs.json()).items).toHaveLength(1);
    expect(
      (
        await page.request.get(
          `${endpoint.base_url}/api/v1/runs/${seed.run}/analyses/${seed.first}`,
        )
      ).status(),
    ).toBe(200);
    passed = true;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved repeat project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
