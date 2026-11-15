import { execFileSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

test("application quit leaves virtual-device pending results reachable in the same window", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-direct-journey-"));
  const env: NodeJS.ProcessEnv = { ...process.env, SCOPECAT_LOCATOR_HOME: join(home, "locators") };
  delete env.SCOPECAT_DAEMON_URL;
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
    await expect(
      page.getByRole("combobox", { name: "未保存结果暂存方式", exact: true }),
    ).toHaveValue("memory_only");
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
    const devices = await (await page.request.get(`${url}/api/v1/devices`)).json();
    await page.goto(`${url}/?device-control=${encodeURIComponent(devices.items[0].device.id)}`);
    await page.getByRole("button", { name: "连接", exact: true }).click();
    await expect(page.getByText("已连接", { exact: true })).toBeVisible();
    // A modest 1025-point virtual trace exceeds one preview page without
    // allocating a large load. Saved originals must retain all points.
    await page.getByRole("spinbutton", { name: /Sweep points/ }).fill("1025");
    const applyInitial = page.getByRole("button", { name: "应用暂存修改", exact: true });
    await applyInitial.click();
    await expect(applyInitial).toBeHidden();
    await page.getByTitle("scopecat.network_sweep/v1 / ", { exact: true }).click();
    await expect(page.getByText("1 条待保存", { exact: true })).toBeVisible();
    // Real UI + real virtual-device daemon; only the native host bridge is simulated.
    await page.evaluate(() => {
      const w = globalThis as unknown as {
        dispatchEvent(event: Event): boolean;
        pywebview: unknown;
        scopecatRequestExit: () => void;
      };
      let resolveExit: ((value: "cancelled") => void) | undefined;
      w.pywebview = {
        api: {
          set_window_title: async () => {},
          exit_status: async () => ({
            finishing: [],
            deferred: [],
            blockers: [],
            file_operations: 0,
          }),
          request_exit: () =>
            new Promise((complete) => {
              resolveExit = complete;
              w.dispatchEvent(
                new CustomEvent("scopecat:close-preflight", { detail: { requestId: "test-quit" } }),
              );
            }),
          complete_close_preflight: async () => {},
          cancel_close_preflight: async () => {
            w.dispatchEvent(
              new CustomEvent("scopecat:close-release", { detail: { requestId: "test-quit" } }),
            );
            resolveExit?.("cancelled");
            return true;
          },
        },
      };
      w.dispatchEvent(new Event("pywebviewready"));
      w.scopecatRequestExit();
    });
    const review = page.getByRole("dialog", { name: "结束控制前的待办", exact: true });
    await expect(review).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await expect(
      review.getByRole("button", { name: "保存全部未保存结果", exact: true }),
    ).toBeEnabled();
    await review.getByRole("button", { name: "取消本次关闭等待", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Quit Scopecat" })).toHaveCount(0);
    await expect(page.getByText("1 条待保存", { exact: true })).toBeVisible();
    await page.evaluate("window.scopecatRequestExit()");
    await expect(review).toBeVisible();
    await review.getByRole("button", { name: "保存全部未保存结果", exact: true }).click();
    await expect(review.getByRole("heading", { name: "未保存结果：0", exact: true })).toBeVisible();
    await expect(
      review.getByRole("button", { name: "保持当前状态并结束", exact: true }),
    ).toBeEnabled();
    await testInfo.attach("same-window-device-review", {
      body: await page.screenshot({ path: testInfo.outputPath("same-window-device-review.png") }),
      contentType: "image/png",
    });
    await review.getByRole("button", { name: "取消本次关闭等待", exact: true }).click();
    const records = await (await page.request.get(`${url}/api/v1/direct-measurements`)).json();
    expect(records.items).toHaveLength(1);
  } finally {
    if (started) command("stop");
    await rm(home, { recursive: true, force: true });
  }
});
