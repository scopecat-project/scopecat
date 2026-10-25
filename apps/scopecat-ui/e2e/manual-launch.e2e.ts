import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { chooseReferenceContext, prepareReferenceContexts } from "./reference-context";

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

test("registered devices share ownership across pages without setup selection", async ({
  page,
  context,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-device-context-e2e-"));
  let completed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "testing/reference_lab", name), join(project, name), {
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
    prepareReferenceContexts(uv, project);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    await page.goto(`${endpoint.base_url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Device ownership tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    const second = await context.newPage();
    await page.goto(`${endpoint.base_url}/#instruments`);
    await second.goto(`${endpoint.base_url}/#instruments`);
    await expect(page.getByLabel("实验配置", { exact: true })).toHaveCount(0);
    await page.getByTitle("查看仪器 drive-lo-a").click();
    await second.getByTitle("查看仪器 drive-lo-a").click();
    const opening = page.waitForRequest((request) =>
      request.url().endsWith("/instrument-sessions"),
    );
    await page.getByRole("button", { name: "连接", exact: true }).click();
    const deviceSetup = (await opening).postDataJSON().setup;
    expect(deviceSetup.revision_id).toMatch(/^resolved:/);
    await expect(page.getByText("交互会话已连接")).toBeVisible();
    const inventory = await (
      await second.request.get(`${endpoint.base_url}/api/v1/devices`)
    ).json();
    const device = inventory.items.find(
      (item: { device: { id: string } }) => item.device.id === "drive-lo-a",
    );
    const blocked = await second.request.post(
      `${endpoint.base_url}/api/v1/devices/drive-lo-a/connection-tests`,
      {
        data: {
          expected_head: device.device.head,
          actor: "browser-test",
          operation_id: "competing-probe",
        },
      },
    );
    expect(blocked.status()).toBe(409);
    expect(await blocked.text()).toContain("resources are busy");
    await second.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(second.getByRole("button", { name: "连接", exact: true })).not.toBeVisible();
    await page.getByRole("button", { name: "断开连接", exact: true }).click();
    await second.getByRole("button", { name: "刷新", exact: true }).click();
    const nextOpening = second.waitForRequest((request) =>
      request.url().endsWith("/instrument-sessions"),
    );
    await second.getByRole("button", { name: "连接", exact: true }).click();
    expect((await nextOpening).postDataJSON().setup).toEqual(deviceSetup);
    await expect(second.getByText("交互会话已连接")).toBeVisible();
    await second.getByRole("button", { name: "断开连接", exact: true }).click();
    await second.getByRole("button", { name: "测试连接", exact: true }).click();
    await expect(second.getByText(/连接测试通过，测试会话已释放/)).toBeVisible();
    await second.close();
    completed = true;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved device context project", {
        body: project,
        contentType: "text/plain",
      });
  }
});

test("manual changes invalidate a retained preview before a fresh acquisition", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-manual-e2e-"));
  let completed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "testing/reference_lab", name), join(project, name), {
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
    prepareReferenceContexts(uv, project);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    await page.goto(`${endpoint.base_url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Device preview tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.reload();
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("ramsey");
    await chooseReferenceContext(page);
    await page.getByLabel("Delay", { exact: true }).fill("64");
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeEnabled();
    uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
from scopecat.application import LabApplication
from scopecat_instruments import rf_source
with LabApplication().connect(sys.argv[1]) as lab:
    target = rf_source("drive-lo-a")
    with lab.devices.open("drive-lo-a") as devices:
        observed = devices[target].frequency.read_observation()
        assert observed.source == "hardware_query"
        assert devices[target].apply(frequency=sc.Quantity(4.95, "GHz")).status == "applied"
`,
      endpoint.base_url,
    ]);
    await expect(page.getByText(/Manual instrument changes invalidate this preview/)).toBeVisible();
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeEnabled();
    await expect(page.getByLabel("Delay", { exact: true })).toHaveValue("64");
    const submitted = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/submit"),
    );
    await page.getByRole("button", { name: "开始采集", exact: true }).click();
    expect((await submitted).status()).toBe(200);
    await expect(page.getByText("experiment: 已完成", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: /^查看已保存结果:/ }).click();
    await expect(page.getByTestId("run-status")).toHaveText("已完成");
    completed = true;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved manual project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
