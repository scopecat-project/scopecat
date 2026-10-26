import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", root, ...args], {
    encoding: "utf8",
    env,
    timeout: 30_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
}
async function start(project: string) {
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
  return (
    JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    }
  ).base_url;
}

test("share without runs, inspect/cancel, derive in an empty receiver and edit normally", async ({
  page,
}) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(10_000);
  const sender = await mkdtemp(join(tmpdir(), "scopecat-exchange-sender-"));
  const receiver = await mkdtemp(join(tmpdir(), "scopecat-exchange-receiver-"));
  const started: string[] = [];
  try {
    uv(["scopecat", "init", sender]);
    const senderUrl = await start(sender);
    started.push(sender);
    // Author saves initial inputs through the ordinary public SDK; no acquisition.
    uv([
      "python",
      "-c",
      `
import sys
from pathlib import Path
import scopecat as sc
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from scopecat_lab.authored.parameters import initial_parameters
content = initial_parameters()
with sc.open_project(root).connect() as lab:
    lab.parameters.save(name="starter-initial", catalog=content.parameter_catalog, parameters=content.parameter_snapshot)
`,
      sender,
    ]);
    // Recipient has no bootstrap, devices, parameter catalog or source registration.
    await writeFile(join(receiver, "scopecat.toml"), "[lab]\n");
    const receiverUrl = await start(receiver);
    started.push(receiver);
    await page.goto(`${senderUrl}/#config`);
    await page.getByRole("button", { name: /Configuration$/, exact: true }).click();
    await page.getByRole("button", { name: "导出配置…", exact: true }).click();
    await page
      .getByRole("combobox", { name: "参数版本", exact: true })
      .selectOption("starter-initial");
    await page.getByLabel("文件标签", { exact: true }).fill("Colleague inputs");
    await page.getByRole("button", { name: "核对导出内容", exact: true }).click();
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "保存配置文件", exact: true }).click();
    const file = await (await downloading).path();
    expect(file).toBeTruthy();
    await page.goto(`${receiverUrl}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Configuration regression tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /Configuration$/, exact: true }).click();
    // The mounted form keeps its original attribution; explicitly set this operation.
    await page.getByLabel("操作者", { exact: true }).fill("Configuration regression tester");
    const upload = page.getByLabel("导入配置文件");
    await upload.setInputFiles(file);
    await expect(page.getByText("包含初始值", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "取消检查", exact: true }).click();
    expect(
      await (await page.request.get(`${receiverUrl}/api/v1/configuration-exchange/imports`)).json(),
    ).toEqual([]);
    expect(
      (await (await page.request.get(`${receiverUrl}/api/v1/parameters/revisions`)).json()).items,
    ).toEqual([]);
    await upload.setInputFiles(file);
    await page.getByLabel("新参数分支名称").fill("My inputs");
    await page.getByRole("button", { name: "创建我的副本", exact: true }).click();
    await expect(page.getByText(/已创建参数分支“My inputs”/)).toBeVisible();
    await expect(page.getByRole("combobox", { name: "已保存的参数版本", exact: true })).toHaveValue(
      "My inputs",
    );
    await page.getByRole("button", { name: "编辑副本", exact: true }).click();
    await page.getByRole("textbox", { name: "response[1].scale", exact: true }).fill("2");
    await page.getByText("保存参数检查点", { exact: true }).click();
    await page.getByLabel("新版本名称", { exact: true }).fill("My adjusted inputs");
    await page.getByRole("button", { name: "保存参数版本", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "已保存的参数版本", exact: true })).toHaveValue(
      "My adjusted inputs",
    );
    await page.reload();
    await page.getByText("已导入的原件（最近 100 条）", { exact: true }).click();
    await page.getByRole("button", { name: "Colleague inputs", exact: true }).click();
    await page.getByLabel("新参数分支名称").fill("My inputs");
    await page.getByRole("button", { name: "创建我的副本", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("already uses this name");
    await page.getByLabel("新参数分支名称").fill("My second copy");
    await page.getByRole("button", { name: "创建我的副本", exact: true }).click();
    await expect(page.getByText(/已创建参数分支“My second copy”/)).toBeVisible();
    const originals = await (
      await page.request.get(`${receiverUrl}/api/v1/configuration-exchange/imports`)
    ).json();
    expect(originals).toHaveLength(1);
    expect(originals[0].derivations).toHaveLength(2);
  } finally {
    for (const project of started.toReversed())
      uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    await Promise.all(
      [sender, receiver].map((project) => rm(project, { recursive: true, force: true })),
    );
  }
});
