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
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: /Devices and drivers$/, exact: true }).click();
    await page.getByRole("button", { name: "Add device", exact: true }).click();
    const dialog = page.getByRole("dialog");
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const name = dialog.getByLabel("Device name");
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
    await expect(page.getByRole("button", { name: "Add device", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "Add device", exact: true }).click();
    await dialog.getByLabel("Device name").fill("Reopen virtual device");
    await dialog.getByLabel("Gain", { exact: true }).fill("-1");
    await dialog.getByRole("button", { name: "Save device", exact: true }).click();
    await expect(dialog).toBeHidden();
    await page.getByRole("button", { name: "Test connection", exact: true }).click();
    await expect(page.getByText(/Last connection test failed:/)).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(
      "instrument connection could not be established",
    );
    const failed = await current();
    expect(failed.last_connection_test?.revision).toEqual(failed.device.head);

    await page.getByRole("button", { name: "Edit device", exact: true }).click();
    await dialog.getByLabel("Gain", { exact: true }).fill("2");
    await dialog.getByRole("button", { name: "Save device", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("This connection version has not been tested.")).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    const edited = await current();
    expect(edited.device.head).not.toEqual(failed.device.head);
    expect(edited.last_connection_test).toBeNull();

    await page.reload();
    await expect(page.getByText("This connection version has not been tested.")).toBeVisible();
    await page.getByRole("button", { name: "Test connection", exact: true }).click();
    await expect(page.getByText(/Connection test passed;/)).toBeVisible();
    const tested = await current();
    expect(tested.last_connection_test?.revision).toEqual(edited.device.head);
    expect(tested.last_connection_test?.error).toBeNull();

    await page.goto("about:blank");
    await stop();
    await start();
    await page.goto(url);
    await page.getByRole("button", { name: /Devices and drivers$/, exact: true }).click();
    await expect(page.getByText(/Connection test passed;/)).toBeVisible();
    expect((await current()).last_connection_test).toEqual(tested.last_connection_test);
    await page.getByRole("button", { name: "Edit device", exact: true }).click();
    await expect(dialog.getByLabel("Gain", { exact: true })).toHaveValue("2");
  } finally {
    if (daemon! && daemon.exitCode === null) await stop();
    await rm(root, { recursive: true, force: true });
  }
});
