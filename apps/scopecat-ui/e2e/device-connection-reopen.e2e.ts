import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import type { components } from "../src/api-schema";
type DeviceView = components["schemas"]["DeviceView"];

test("connection test feedback follows the saved revision across editing and daemon restart", async ({
  page,
}, testInfo) => {
  const root = await mkdtemp(join(tmpdir(), "scopecat-device-reopen-"));
  let daemon: ReturnType<typeof spawn>;
  let stopped: Promise<void>;
  let url = "";
  async function start() {
    daemon = spawn(
      "uv",
      [
        "run",
        "--locked",
        "--project",
        "../..",
        "python",
        "../../packages/scopecat-server/tests/fixtures/device_save_daemon.py",
        root,
        resolve("dist"),
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    let logs = "";
    daemon.stderr!.on("data", (chunk) => {
      logs += String(chunk);
    });
    stopped = new Promise<void>((done) => daemon.once("exit", () => done()));
    await expect
      .poll(async () => {
        if (daemon.exitCode !== null) throw new Error(logs);
        try {
          url = await readFile(join(root, "url"), "utf8");
          return (await page.request.get(`${url}/api/v1/health`)).ok();
        } catch {
          return false;
        }
      })
      .toBe(true);
  }
  async function stop() {
    daemon.kill("SIGTERM");
    await stopped;
  }
  async function current(): Promise<DeviceView> {
    return (await (await page.request.get(`${url}/api/v1/devices`)).json()).items[0];
  }
  try {
    await start();
    await page.goto(`${url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Device regression tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: /Devices and drivers$/, exact: true }).click();
    await page.getByRole("button", { name: "添加设备", exact: true }).click();
    const dialog = page.getByRole("dialog");
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const name = dialog.getByLabel("设备名称");
      await name.focus();
      await expect(name).toHaveCSS("outline-width", "2px");
      await expect(name).toHaveCSS("font-size", "14px");
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await testInfo.attach(`device-dialog-${width}`, {
        body: await page.screenshot({
          fullPage: true,
          ...(process.env.SCOPECAT_SCREENSHOTS
            ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/device-dialog-${width}.png` }
            : {}),
        }),
        contentType: "image/png",
      });
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("button", { name: "添加设备", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "添加设备", exact: true }).click();
    await dialog.getByLabel("设备名称").fill("Reopen virtual device");
    await dialog.getByLabel("Gain", { exact: true }).fill("-1");
    await dialog.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(dialog).toBeHidden();
    await page.getByRole("button", { name: "测试连接", exact: true }).click();
    await expect(page.getByText(/上次连接测试失败：/)).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(
      "instrument connection could not be established",
    );
    const failed = await current();
    expect(failed.last_connection_test?.revision).toEqual(failed.device.head);

    await page.getByRole("button", { name: "编辑设备", exact: true }).click();
    await dialog.getByLabel("Gain", { exact: true }).fill("2");
    await dialog.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("此连接版本尚未测试。")).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    const edited = await current();
    expect(edited.device.head).not.toEqual(failed.device.head);
    expect(edited.last_connection_test).toBeNull();

    await page.reload();
    await expect(page.getByText("此连接版本尚未测试。")).toBeVisible();
    await page.getByRole("button", { name: "测试连接", exact: true }).click();
    await expect(page.getByText(/连接测试通过，/)).toBeVisible();
    const tested = await current();
    expect(tested.last_connection_test?.revision).toEqual(edited.device.head);
    expect(tested.last_connection_test?.error).toBeNull();

    // Load a previous configuration without publishing or touching hardware.
    await page.getByRole("button", { name: "编辑设备", exact: true }).click();
    await dialog.getByRole("button", { name: "使用上一连接版本生成草稿" }).click();
    await expect(dialog.getByLabel("Gain", { exact: true })).toHaveValue("-1");
    await expect(dialog.getByRole("region", { name: "连接修改预览" })).toContainText("驱动选项");
    await expect(dialog.getByRole("region", { name: "连接修改预览" })).toContainText(
      "驱动选项 · Gain",
    );
    await expect(dialog.getByRole("region", { name: "连接修改预览" })).toContainText("2 → -1");
    await expect(dialog.getByRole("region", { name: "连接修改预览" })).not.toContainText('{"gain"');
    expect((await current()).device.head).toEqual(tested.device.head);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await dialog.getByRole("region", { name: "连接修改预览" }).scrollIntoViewIfNeeded();
      await expect(dialog.getByRole("heading", { name: "编辑设备" })).toBeInViewport();
      await expect(dialog.getByRole("button", { name: "保存设备", exact: true })).toBeInViewport();
      await testInfo.attach(`connection-recovery-${width}`, {
        body: await page.screenshot({
          fullPage: true,
          ...(process.env.SCOPECAT_SCREENSHOTS
            ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/connection-recovery-${width}.png` }
            : {}),
        }),
        contentType: "image/png",
      });
    }
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    expect((await current()).device.head).toEqual(tested.device.head);
    // Reopen, restore as a new revision, fail its test, then repair in place.
    await page.getByRole("button", { name: "编辑设备", exact: true }).click();
    await dialog.getByRole("button", { name: "使用上一连接版本生成草稿" }).click();
    await dialog.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(dialog).toBeHidden();
    const restored = await current();
    expect(restored.revision.content).toEqual(failed.revision.content);
    expect(restored.device.head).not.toEqual(failed.device.head);
    expect(restored.revision.previous).toEqual(tested.device.head);
    await page.getByRole("button", { name: "测试连接", exact: true }).click();
    await expect(page.getByText(/上次连接测试失败：/)).toBeVisible();
    await page.getByRole("button", { name: "编辑设备", exact: true }).click();
    await dialog.getByRole("button", { name: "使用上一连接版本生成草稿" }).click();
    await dialog.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(dialog).toBeHidden();
    await page.getByRole("button", { name: "测试连接", exact: true }).click();
    await expect(page.getByText(/连接测试通过，/)).toBeVisible();
    const finalTested = await current();

    await page.goto("about:blank");
    await stop();
    await start();
    await page.goto(`${url}/#instruments`);
    await expect(page.getByText(/连接测试通过，/)).toBeVisible();
    expect((await current()).last_connection_test).toEqual(finalTested.last_connection_test);
    await page.getByRole("button", { name: "编辑设备", exact: true }).click();
    await expect(dialog.getByLabel("Gain", { exact: true })).toHaveValue("2");
  } finally {
    if (daemon! && daemon.exitCode === null) await stop();
    await rm(root, { recursive: true, force: true });
  }
});
