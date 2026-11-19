import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { expectRunIdentity } from "./run-identity";

test.use({
  video: process.env.SCOPECAT_QA_CHROMIUM ? "off" : "retain-on-failure",
  launchOptions: process.env.SCOPECAT_QA_CHROMIUM
    ? { executablePath: process.env.SCOPECAT_QA_CHROMIUM }
    : {},
});

// Preserve this isolated application and author evidence for native follow-up.
test("blank application adds Virtual VNA in UI and runs the copied declaration without named setup", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(240_000);
  const home = await mkdtemp(join(process.env.SCOPECAT_QA_ROOT ?? tmpdir(), "scopecat-vna-entry-"));
  const source = join(home, "authors");
  const env: NodeJS.ProcessEnv = { ...process.env, SCOPECAT_LOCATOR_HOME: join(home, "locators") };
  delete env.SCOPECAT_DAEMON_URL;
  const uv = (...args: string[]) =>
    execFileSync("uv", ["run", "--locked", "--project", "../..", ...args], {
      env,
      encoding: "utf8",
      timeout: 90_000,
    }).trim();
  const app = (action: string, ...args: string[]) =>
    uv("scopecat", "app", "--action", action, "--home", home, ...args);
  await testInfo.attach("isolated-home", { body: home, contentType: "text/plain" });
  let started = false;
  let cleanupError: unknown;
  try {
    app("configure", "--static-dir", resolve("dist"));
    const url = app("start").split("\n").at(-1)!;
    started = true;
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: url });
    await page.goto(`${url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Virtual VNA entry QA");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: "启用模拟设备体验（无硬件）", exact: true }).click();
    await expect(page.getByText("内置模拟设备（无硬件）", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "添加设备", exact: true }).click();
    const editor = page.getByRole("dialog");
    await editor.getByLabel("设备名称").fill("Entry Virtual VNA");
    await editor
      .getByRole("combobox", { name: "驱动", exact: true })
      .selectOption("scopecat.virtual.vna");
    await editor.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(editor).toBeHidden();
    const devices = (await (await page.request.get(`${url}/api/v1/devices`)).json()).items;
    expect(devices).toHaveLength(1);
    const id = devices[0].device.id as string;
    expect(devices[0].revision.content.connection.kind).toBe("virtual");
    await page.getByRole("button", { name: "操作", exact: true }).click();
    await page.getByRole("button", { name: "Python 示例", exact: true }).click();
    await page.getByRole("button", { name: "复制实验声明 Python 整机", exact: true }).click();
    const copied = await page.evaluate<string>("navigator.clipboard.readText()");
    const guideSource = await readFile("src/features/instruments/DevicePythonGuide.tsx", "utf8");
    const exactSnippet = guideSource.split("    experiment: `")[1].split("`,")[0];
    expect(copied).toBe(exactSnippet);
    await writeFile(join(home, "copied-experiment.py"), copied);
    await page.screenshot({ path: testInfo.outputPath("python-guide.png"), fullPage: true });
    // Authoring is separate from registration. No devices or setups are seeded here.
    const workspace = uv(
      "python",
      "-c",
      `
import sys
from pathlib import Path
import scopecat as sc
from scopecat_app.application_runtime import ApplicationRuntime
from scopecat_server.scaffold import write_author_scaffold
home=Path(sys.argv[1]); source=Path(sys.argv[2])
write_author_scaffold(source)
(source/'src/scopecat_lab/authored/capture.py').write_text((home/'copied-experiment.py').read_text())
workspace=ApplicationRuntime(home).register_source(source)
sys.path.insert(0,str(source/'src'))
with sc.open_project(source).authoring() as author:
    author.refresh()
    from scopecat_lab.authored.parameters import initial_parameters
    content=initial_parameters()
    revision=author.parameters.save(name='entry-values',catalog=content.parameter_catalog,parameters=content.parameter_snapshot)
    author.parameters.create_branch('entry',revision=revision)
    assert author.setup.list()==()
print(workspace)
`,
      home,
      source,
    )
      .split("\n")
      .at(-1)!;
    await page.goto(`${url}/?workspace=${workspace}#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("capture");
    await page.getByRole("button", { name: "选择参数分支", exact: true }).click();
    await page.getByLabel("参数分支", { exact: true }).selectOption("entry");
    await page.getByRole("button", { name: "使用此参数版本", exact: true }).click();
    await page.getByRole("button", { name: "检查本实验所需设备", exact: true }).click();
    const endpoint = page.getByRole("combobox", {
      name: "设备与端点 · network_sweep",
      exact: true,
    });
    await expect(endpoint).toHaveValue("");
    await endpoint.selectOption("0");
    await page
      .getByRole("combobox", { name: "开始时 · network_sweep", exact: true })
      .selectOption("preserve");
    await page
      .getByRole("combobox", { name: "成功后 · network_sweep", exact: true })
      .selectOption("release");
    await page
      .getByRole("combobox", { name: "失败后 · network_sweep", exact: true })
      .selectOption("abort_and_release");
    await page.getByLabel("这是软件模拟，需要记录模型及局限", { exact: true }).check();
    for (const [label, value] of [
      ["模拟名称", "Entry Virtual VNA"],
      ["模型 ID", "scopecat.virtual.vna"],
      ["模型版本", "v1"],
      ["模拟能力（每行一项）", "Synthetic S21"],
      ["模拟局限（每行一项）", "No physical qualification"],
    ]) {
      await page.getByRole("textbox", { name: label, exact: true }).fill(value);
    }
    await page.getByRole("button", { name: "检查并用于本次准备", exact: true }).click();
    await expect(page.getByText(/绑定检查通过/)).toBeVisible();
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath("prepared-vna.png"), fullPage: true });
    await page.getByRole("button", { name: "开始采集", exact: true }).click();
    await expect(page.getByText("experiment: 已完成", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    const result = page.getByRole("link", { name: /^查看已保存结果:/ });
    const runId = new URL((await result.getAttribute("href"))!, url).searchParams.get("run")!;
    await result.click();
    await expectRunIdentity(page, runId);
    await page.reload();
    await expectRunIdentity(page, runId);
    const pythonResult = uv(
      "python",
      "-c",
      `
import importlib,json,sys
from pathlib import Path
import scopecat as sc
from scopecat.records.instrument_bindings import InstrumentBindingPlan
from scopecat.records.execution_scenario import SoftwareExecutionScenario
source=Path(sys.argv[1]);sys.path.insert(0,str(source/'src'))
with sc.open_project(source).authoring() as author:
    gui=author.run(sys.argv[3])
    assert len(gui.measurements()['frequency'].require_magnitudes('Hz')[0])==51
    assert author.setup.list()==()
    author.use(parameter_branch='entry',setup=None)
    request=importlib.import_module('scopecat_lab.authored.capture').capture()
    requirements=author.binding_requirements(request)
    assert requirements.supported and requirements.requirements[0].role_id=='demo-readout'
    candidate=next(c for c in author.setup.binding_options(requirements).candidates if c.device_id==sys.argv[2] and c.component_path==())
    plan=InstrumentBindingPlan(requirements=requirements,choices=(candidate.select(run_start='preserve',success_action='release',failure_action='abort_and_release'),),scenario=SoftwareExecutionScenario(id='entry-python',label='Entry Python Virtual VNA',model_id='scopecat.virtual.vna',model_version='v1',capabilities=('Synthetic S21',),limitations=('No physical qualification',)))
    job=author.prepare(request,instrument_bindings=plan).run()
    run=job.wait(timeout=90).result()
    assert len(run.measurements()['frequency'].require_magnitudes('Hz')[0])==51
    assert author.setup.list()==()
    print(json.dumps({'gui_run':gui.id,'python_run':run.id,'receipt':str(job.receipt),'device_id':sys.argv[2]}))
`,
      source,
      id,
      runId,
    )
      .split("\n")
      .at(-1)!;
    await writeFile(join(home, "evidence.json"), pythonResult);
    const pythonRun = (JSON.parse(pythonResult) as { python_run: string }).python_run;
    await page.getByRole("button", { name: /Data$/, exact: true }).click();
    const opening = context.waitForEvent("page");
    await page.locator(`a[href*="run=${pythonRun}"]`).click();
    const resultWindow = await opening;
    await expectRunIdentity(resultWindow, pythonRun);
    await resultWindow.reload();
    await expectRunIdentity(resultWindow, pythonRun);
    await resultWindow.screenshot({
      path: testInfo.outputPath("python-result.png"),
      fullPage: true,
    });
  } finally {
    if (started) {
      try {
        app("stop");
      } catch (error) {
        await testInfo.attach("stop-error", { body: String(error), contentType: "text/plain" });
        cleanupError = error;
      }
    }
    await testInfo.attach("retained-evidence", { body: home, contentType: "text/plain" });
  }
  if (cleanupError) throw cleanupError;
});
