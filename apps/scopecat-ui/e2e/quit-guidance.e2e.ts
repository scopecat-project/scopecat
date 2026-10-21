import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { startProject, stopProject, daemon } from "./daemon";

test("automatic quit explains unfinished work and keeps cancellation reachable", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-quit-guidance-"));
  try {
    daemon(["init", project]);
    const url = await startProject(project);
    // The browser exercises the shipped dialog; the native bridge is a controlled stand-in.
    await page.addInitScript(() => {
      let cancelFails = true;
      Object.assign(globalThis, {
        pywebview: {
          api: {
            request_exit: async () => ({ procedures: 2, runs: 0, instrument_sessions: 0 }),
            wait_for_idle: async (wait: boolean) => {
              if (!wait && cancelFails) {
                cancelFails = false;
                throw new Error("无法连接服务：自动退出尚未取消");
              }
            },
          },
        },
      });
    });
    await page.goto(url);
    await page.keyboard.press("Control+q");
    const dialog = page.getByRole("dialog", { name: "Quit Scopecat" });
    await expect(dialog).toContainText("Unfinished work: 2 workflows");
    await page.screenshot({ path: testInfo.outputPath("quit-before-wait.png"), fullPage: true });
    await expect(dialog).toContainText("Unfinished does not always mean executing");
    await dialog.getByRole("button", { name: "Quit when work finishes", exact: true }).click();
    await expect(dialog.getByRole("status")).toContainText("does not answer reviews");
    await page.setViewportSize({ width: 390, height: 844 });
    await dialog.getByRole("button", { name: "Cancel", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(dialog.getByRole("alert")).toContainText("自动退出尚未取消");
    await expect(dialog.getByRole("status")).toContainText("Automatic quit is enabled");
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({
      path: testInfo.outputPath("quit-cancel-error-narrow.png"),
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await page.keyboard.press("Control+q");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(dialog).toBeHidden();
  } finally {
    stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
