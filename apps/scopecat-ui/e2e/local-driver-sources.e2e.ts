import { execFileSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

test("a fresh standard application discovers simulation and combines public sources", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(15_000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-local-sources-"));
  const env = { ...process.env, SCOPECAT_LOCATOR_HOME: join(home, "locators") };
  const command = (action: string, ...args: string[]) =>
    execFileSync(
      "uv",
      [
        "run",
        "--locked",
        "--project",
        "../..",
        "scopecat",
        "app",
        "--action",
        action,
        "--home",
        home,
        ...args,
      ],
      { env, encoding: "utf8", timeout: 60_000 },
    );
  let started = false;
  try {
    command("configure", "--static-dir", resolve("dist"));
    let url = command("start").trim().split("\n").at(-1)!;
    started = true;
    await page.goto(`${url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Simulation source tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: "启用模拟设备体验（无硬件）", exact: true }).click();
    await expect(page.getByText("内置模拟设备（无硬件）", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "添加设备", exact: true }).click();
    const editor = page.getByRole("dialog");
    await editor.getByLabel("设备名称").fill("Explicit simulated RF");
    await expect(editor.getByRole("combobox", { name: "驱动", exact: true })).toHaveValue(
      "scopecat.virtual.rf_source",
    );
    await editor.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(editor).toBeHidden();
    await page.getByRole("button", { name: "操作", exact: true }).click();
    await page.getByRole("button", { name: "连接", exact: true }).click();
    await expect(page.getByText("交互会话已连接", { exact: true })).toBeVisible();
    const frequency = page.getByRole("spinbutton", { name: /CW frequency/ });
    await frequency.fill("5100000000");
    await page.getByRole("button", { name: "应用暂存修改", exact: true }).click();
    await page.getByRole("button", { name: "刷新状态", exact: true }).click();
    await expect(frequency).toHaveValue("5100000000");
    await testInfo.attach("simulated-rf-readback", {
      body: await page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
    await page.getByRole("button", { name: "释放控制", exact: true }).click();
    const finish = page.getByRole("dialog", { name: "结束控制前的待办", exact: true });
    await expect(finish.getByRole("heading", { name: "未保存结果：0", exact: true })).toBeVisible();
    await finish.getByRole("button", { name: "保持当前状态并结束", exact: true }).click();
    await expect(finish).toBeHidden();
    await expect(page.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    await page.getByText("驱动来源与可用驱动", { exact: true }).click();
    await page.getByRole("button", { name: "添加内置公共驱动", exact: true }).click();
    await expect(page.getByText("内置公共驱动", { exact: true })).toBeVisible({ timeout: 30_000 });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate("document.documentElement.scrollWidth")).toBeLessThanOrEqual(
        width,
      );
    }
    command("stop");
    started = false;
    url = command("start").trim().split("\n").at(-1)!;
    started = true;
    await page.goto(`${url}/#instruments`);
    await expect(page.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    await page.getByText("驱动来源与可用驱动", { exact: true }).click();
    await expect(page.getByText("内置模拟设备（无硬件）", { exact: true })).toBeVisible();
    await expect(page.getByText("内置公共驱动", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "连接", exact: true }).click();
    await page.getByRole("button", { name: "刷新状态", exact: true }).click();
    await expect(frequency).toHaveValue("5000000000");
    await page.getByRole("button", { name: "释放控制", exact: true }).click();
    await expect(finish.getByRole("heading", { name: "未保存结果：0", exact: true })).toBeVisible();
    await finish.getByRole("button", { name: "保持当前状态并结束", exact: true }).click();
    await expect(finish).toBeHidden();
    await expect(page.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
  } finally {
    if (started) command("stop");
    await rm(home, { recursive: true, force: true });
  }
});
