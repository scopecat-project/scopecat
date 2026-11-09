import { execFileSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

test("direct VNA results, line changes, retained comparison and exit recover without collecting again", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-direct-journey-"));
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
    const url = command("start").trim().split("\n").at(-1)!;
    started = true;
    await page.goto(`${url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Isolated direct measurement tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: "启用模拟设备体验（无硬件）", exact: true }).click();
    await expect(page.getByText("内置模拟设备（无硬件）", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "添加设备", exact: true }).click();
    const editor = page.getByRole("dialog");
    await editor.getByLabel("设备名称").fill("Isolated synthetic VNA");
    await editor
      .getByRole("combobox", { name: "驱动", exact: true })
      .selectOption("scopecat.virtual.vna");
    await editor.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(editor).toBeHidden();
    await page.getByRole("button", { name: "操作", exact: true }).click();
    await page.getByRole("button", { name: "连接", exact: true }).click();
    await expect(page.getByText("交互会话已连接", { exact: true })).toBeVisible();
    const line = page.getByRole("textbox", { name: "线路名称", exact: true });
    await line.fill("line-a");
    await page.getByRole("checkbox", { name: "已人工确认当前接线为 line-a", exact: true }).check();
    const sweep = page
      .getByTestId("interface-card-scopecat.network_sweep/v1-root")
      .getByRole("button", { name: "收集 Collect", exact: true });
    const read = page
      .getByTestId("interface-card-scopecat.existing_network_trace/v1-root")
      .getByRole("button", { name: "收集 Collect", exact: true });
    let collections = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname.endsWith("/collect"))
        collections += 1;
    });
    await sweep.click();
    const current = page.getByRole("region", { name: "当前测量结果", exact: true });
    await expect(current.getByRole("img", { name: /完整曲线/ })).toBeVisible();
    const original = await current.getByText(/完整 \d+ 点/).textContent();
    await current.getByText("结果数据（数值与可用性）", { exact: true }).click();
    await current
      .getByRole("combobox", { name: "精确结果字段", exact: true })
      .selectOption("s_parameter");
    await expect(current.getByRole("table")).toContainText('"real"');
    await current
      .getByRole("combobox", { name: "精确结果字段", exact: true })
      .selectOption("frequency");
    await expect(current.getByRole("table")).toBeVisible();
    await expect(current.getByRole("button", { name: "下一页数据", exact: true })).toBeEnabled();
    await current.getByRole("button", { name: "下一页数据", exact: true }).click();
    await expect(current.getByText(/显示第 101/)).toBeVisible();
    await current.getByText("结果数据（数值与可用性）", { exact: true }).click();
    await page.getByRole("spinbutton", { name: /Sweep points/ }).fill("3");
    await page.getByRole("button", { name: "应用暂存修改", exact: true }).click();
    await expect(current.getByText(/此后已执行设置或设备动作/)).toBeVisible();
    await read.click();
    await expect(current.getByText(/读取已有曲线（未触发新采集）/)).toBeVisible();
    await expect(current.getByText(/完整 \d+ 点/)).toHaveText(original!);
    await expect(page.getByRole("img", { name: /完整曲线/ })).toHaveCount(1);
    await page.getByRole("button", { name: "开始下一条线路…", exact: true }).click();
    await expect(line).toHaveValue("line-a");
    await page.getByRole("button", { name: "取消换线", exact: true }).click();
    await page.getByRole("button", { name: "释放控制", exact: true }).click();
    const exit = page.getByRole("dialog", { name: "结束控制前的待办", exact: true });
    await expect(exit).toBeVisible();
    await expect(
      exit.getByRole("button", { name: "保持当前状态并结束", exact: true }),
    ).toBeDisabled();
    let saves = 0;
    await page.route("**/api/v1/direct-measurements", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      saves += 1;
      if (saves === 2)
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "isolated save interruption" }),
        });
      return route.continue();
    });
    await exit.getByRole("button", { name: "保存全部未保存结果", exact: true }).click();
    await expect(exit.getByRole("heading", { name: "未保存结果：1", exact: true })).toBeVisible();
    await expect(exit.getByRole("alert")).toContainText("保存失败");
    await exit.getByRole("button", { name: "保存全部未保存结果", exact: true }).click();
    await expect(exit.getByRole("heading", { name: "未保存结果：0", exact: true })).toBeVisible();
    expect(saves).toBe(3);
    expect(collections).toBe(2);
    await exit.getByRole("button", { name: "继续控制", exact: true }).click();
    await expect(exit).toBeHidden();
    const records = page.getByRole("checkbox", { name: /选择记录/ });
    await expect(records).toHaveCount(2);
    await records.nth(0).check();
    await records.nth(1).check();
    await expect(page.getByRole("img", { name: /s_parameter 同轴比较/ })).toBeVisible();
    await page.getByRole("button", { name: "开始下一条线路…", exact: true }).click();
    await page.getByRole("textbox", { name: "下一条线路名称", exact: true }).fill("line-b");
    await page.getByRole("button", { name: "切换并确认接线", exact: true }).click();
    await expect(sweep).toBeDisabled();
    await page.getByRole("checkbox", { name: "已人工确认当前接线为 line-b", exact: true }).check();
    await sweep.click();
    await expect(current.getByText(/完整 3 点/)).toBeVisible();
    await page.getByRole("button", { name: "保存此结果", exact: true }).click();
    await expect(records).toHaveCount(3);
    await records.nth(0).check();
    await page.getByRole("button", { name: "释放控制", exact: true }).click();
    await exit.getByRole("button", { name: "保持当前状态并结束", exact: true }).click();
    await expect(page.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    await page.reload();
    await expect(records).toHaveCount(3);
    await records.nth(0).check();
    await records.nth(2).check();
    await expect(page.getByRole("img", { name: /s_parameter 同轴比较/ })).toBeVisible();
    await testInfo.attach("direct-measurement-retained-comparison", {
      body: await page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  } finally {
    if (started) command("stop");
    await rm(home, { recursive: true, force: true });
  }
});
