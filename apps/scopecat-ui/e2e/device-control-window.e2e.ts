import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import type { components } from "../src/api-schema";
type DeviceView = components["schemas"]["DeviceView"];

test("device control windows hand off sessions without sharing writable views", async ({
  page,
  context,
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
  const controls: Page[] = [];
  async function capture(view: Page, name: string) {
    await testInfo.attach(name, {
      body: await view.screenshot({
        fullPage: true,
        ...(process.env.SCOPECAT_SCREENSHOTS
          ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/${name}.png` }
          : {}),
      }),
      contentType: "image/png",
    });
  }
  try {
    await start();
    await page.goto(`${url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Control window tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: /Devices and drivers$/, exact: true }).click();
    for (const name of ["Bench RF fixture", "Integrated controller fixture"]) {
      await page.getByRole("button", { name: "添加设备", exact: true }).click();
      const editor = page.getByRole("dialog");
      await editor.getByLabel("设备名称").fill(name);
      await editor.getByRole("button", { name: "保存设备", exact: true }).click();
      await expect(editor).toBeHidden();
    }
    const devices: DeviceView[] = (await (await page.request.get(`${url}/api/v1/devices`)).json())
      .items;
    expect(devices).toHaveLength(2);
    // The JS bridge is a software stand-in; native registry behavior is covered in Python.
    await context.addInitScript(() => {
      const state = globalThis as unknown as {
        pywebview: unknown;
        closeReplies: unknown[];
        openedDevices: string[];
      };
      state.closeReplies = [];
      state.openedDevices = [];
      state.pywebview = {
        api: {
          open_device_window: async (id: string) => {
            state.openedDevices.push(id);
          },
          set_window_title: async () => {},
          complete_close_preflight: async (id: string, status: string) => {
            state.closeReplies.push({ id, status });
          },
        },
      };
    });
    await page.reload();
    await page.getByRole("button", { name: "打开控制窗口", exact: true }).first().click();
    await page.getByRole("button", { name: "打开控制窗口", exact: true }).first().click();
    await expect(page.getByRole("button", { name: "连接", exact: true })).toHaveCount(0);
    expect(await page.evaluate("openedDevices.length")).toBe(2);
    await capture(page, "01-device-management");
    for (const device of devices) {
      const control = await context.newPage();
      controls.push(control);
      await control.goto(`${url}/?device-control=${encodeURIComponent(device.device.id)}`);
      await expect(
        control.getByRole("heading", { name: device.device.label, exact: true, level: 1 }),
      ).toBeVisible();
      await expect(control.getByRole("button", { name: "添加设备", exact: true })).toHaveCount(0);
      await control.getByRole("button", { name: "连接", exact: true }).click();
      await expect(control.getByText("交互会话已连接", { exact: true })).toBeVisible();
    }
    const control = controls[0];
    await capture(control, "02-connected-controller");
    // A third browser view cannot silently take over the first view's session.
    const outsider = await context.newPage();
    controls.push(outsider);
    await outsider.goto(`${url}/?device-control=${encodeURIComponent(devices[0].device.id)}`);
    await expect(outsider.getByRole("button", { name: "连接", exact: true })).toHaveCount(0);
    await expect(outsider.getByText("手动控制不可用", { exact: true })).toBeVisible();
    // Lost release receipt retains controls and an explicit recovery entry.
    await control.route("**/instrument-sessions/*/close", async (route) => {
      await route.fetch();
      await route.abort("failed");
    });
    await control.evaluate(
      'window.dispatchEvent(new CustomEvent("scopecat:close-preflight", {detail: {requestId: "failed"}}))',
    );
    await expect
      .poll(() => control.evaluate("closeReplies"))
      .toEqual([{ id: "failed", status: "unknown" }]);
    await control.evaluate(
      'window.dispatchEvent(new CustomEvent("scopecat:close-release", {detail: {requestId: "failed"}}))',
    );
    await expect(control.getByText(/释放控制未确认/)).toBeVisible();
    await expect(control.getByRole("button", { name: "释放控制", exact: true })).toBeEnabled();
    for (const width of [1280, 390]) {
      await control.setViewportSize({ width, height: 900 });
      expect(await control.evaluate("document.documentElement.scrollWidth")).toBeLessThanOrEqual(
        width,
      );
      await testInfo.attach(`device-control-recovery-${width}`, {
        body: await control.screenshot({
          fullPage: true,
          ...(process.env.SCOPECAT_SCREENSHOTS
            ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/device-control-recovery-${width}.png` }
            : {}),
        }),
        contentType: "image/png",
      });
    }
    await control.unroute("**/instrument-sessions/*/close");
    await control.evaluate(
      'window.dispatchEvent(new CustomEvent("scopecat:close-preflight", {detail: {requestId: "retry"}}))',
    );
    await expect
      .poll(() => control.evaluate("closeReplies"))
      .toEqual([
        { id: "failed", status: "unknown" },
        { id: "retry", status: "saved" },
      ]);
    await control.evaluate(
      'window.dispatchEvent(new CustomEvent("scopecat:close-release", {detail: {requestId: "retry"}}))',
    );
    await expect(control.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    await capture(control, "04-control-released");
    // Another device stays connected while the first is handed off.
    await expect(controls[1].getByText("交互会话已连接", { exact: true })).toBeVisible();
    await outsider.getByRole("button", { name: "刷新占用", exact: true }).click();
    await outsider.getByRole("button", { name: "连接", exact: true }).click();
    await expect(outsider.getByText("交互会话已连接", { exact: true })).toBeVisible();
    await outsider.getByRole("button", { name: "释放控制", exact: true }).click();
    await controls[1].getByRole("button", { name: "释放控制", exact: true }).click();
    await expect(controls[1].getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    // Main management is one table, with history kept out of the daily list.
    await page.getByTitle(`查看仪器 ${devices[0].device.id}`).click();
    await page.getByRole("button", { name: "停用设备", exact: true }).click();
    await expect(page.getByTitle(`查看仪器 ${devices[0].device.id}`)).toHaveCount(0);
    await page.getByLabel("显示设备").selectOption("retired");
    await page.getByTitle(`查看仪器 ${devices[0].device.id}`).click();
    await expect(page.getByRole("button", { name: "测试连接", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "重新启用", exact: true }).click();
    let restore = page.getByRole("region", { name: "重新启用设备", exact: true });
    await restore.getByRole("button", { name: "取消", exact: true }).click();
    await page.getByRole("button", { name: "重新启用", exact: true }).click();
    restore = page.getByRole("region", { name: "重新启用设备", exact: true });
    await restore.getByRole("button", { name: "保存并启用", exact: true }).click();
    await expect(restore).toBeHidden();
    await expect(page.getByLabel("显示设备")).toHaveValue("available");
    await expect(
      page.getByText("连接配置已保存，尚未测试。请点击“测试连接”；保存不代表设备状态已读回。", {
        exact: true,
      }),
    ).toBeVisible();
    const restoredDevices: DeviceView[] = (
      await (await page.request.get(`${url}/api/v1/devices`)).json()
    ).items;
    expect(restoredDevices).toHaveLength(2);
    const restored = restoredDevices.find((item) => item.device.id === devices[0].device.id)!;
    expect(restored.device.state).toBe("available");
    expect(restored.revision.id).not.toBe(devices[0].revision.id);
    expect(restored.revision.previous).toEqual(devices[0].device.head);
    expect(restored.last_connection_test).toBeNull();
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate("document.documentElement.scrollWidth")).toBeLessThanOrEqual(
        width,
      );
      await capture(page, `device-table-restored-${width}`);
    }
  } finally {
    for (const control of controls) await control.close({ runBeforeUnload: false });
    if (daemon! && daemon.exitCode === null) await stop();
    await rm(root, { recursive: true, force: true });
  }
});
