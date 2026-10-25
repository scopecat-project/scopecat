import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import type { components } from "../src/api-schema";
type DeviceSaveCommand = components["schemas"]["DeviceSaveCommand"];
type DeviceView = components["schemas"]["DeviceView"];

test("a committed save can be retried after response loss without a second revision", async ({
  page,
}) => {
  page.setDefaultTimeout(10_000);
  const root = await mkdtemp(join(tmpdir(), "scopecat-device-retry-"));
  const daemon = spawn(
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
  daemon.stderr.on("data", (chunk) => {
    logs += String(chunk);
  });
  const stopped = new Promise<void>((done) => daemon.once("exit", () => done()));
  try {
    let url = "";
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
    await page.goto(url);
    await page.getByRole("button", { name: /Devices and drivers$/, exact: true }).click();
    await page.getByRole("button", { name: "Add device", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Device name").fill("Retry software device");
    const commands: DeviceSaveCommand[] = [];
    let committed: DeviceView | undefined;
    await page.route("**/api/v1/devices", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      commands.push(route.request().postDataJSON() as DeviceSaveCommand);
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      if (commands.length === 1) {
        committed = (await response.json()) as DeviceView;
        await route.abort("connectionfailed");
      } else await route.fulfill({ response });
    });
    await dialog.getByRole("button", { name: "Save device", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("Save not confirmed");
    const saved = await (await page.request.get(`${url}/api/v1/devices`)).json();
    expect(saved.items).toHaveLength(1);
    expect(saved.items[0].device.head).toEqual(committed!.device.head);
    await dialog.getByRole("button", { name: "Save device", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(commands).toHaveLength(2);
    expect(commands[1]).toEqual(commands[0]);
    const after = await (await page.request.get(`${url}/api/v1/devices`)).json();
    expect(after.items).toHaveLength(1);
    expect(after.items[0].device.head).toEqual(committed!.device.head);
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Retry software device", exact: true }),
    ).toBeVisible();
  } finally {
    daemon.kill("SIGTERM");
    await stopped;
    await rm(root, { recursive: true, force: true });
  }
});
