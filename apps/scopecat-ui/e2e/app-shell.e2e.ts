import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { daemon, startProject, stopProject } from "./daemon";

test("shell labels connection, lazy loading and recovery in Chinese across widths", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-shell-"));
  let started = false;
  let releaseHealth!: () => void;
  const healthGate = new Promise<void>((resolve) => {
    releaseHealth = resolve;
  });
  let releaseHelp!: () => void;
  const helpGate = new Promise<void>((resolve) => {
    releaseHelp = resolve;
  });
  let unavailable = false;
  let status: string | undefined;
  try {
    daemon(["init", project]);
    const base = await startProject(project);
    started = true;
    await page.route("**/api/v1/health", async (route) => {
      await healthGate;
      if (unavailable)
        return route.fulfill({ status: 503, json: { detail: "Synthetic connection loss" } });
      const response = await route.fetch();
      const health = await response.json();
      await route.fulfill({ response, json: { ...health, ...(status ? { status } : {}) } });
    });
    await page.route("**/assets/HelpWorkspace-*.js", async (route) => {
      await helpGate;
      await route.continue();
    });
    await page.goto(`${base}/#help`);
    const top = page.locator(".workspace-top");
    const refresh = top.getByRole("button", { name: "刷新项目数据", exact: true });
    await expect(top.getByRole("status")).toHaveText("正在连接");
    await expect(refresh).toHaveAttribute("aria-busy", "true");
    await expect(page.getByText("正在加载帮助…", { exact: true })).toBeVisible();
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() => page.evaluate<number>("document.documentElement.scrollWidth"))
        .toBeLessThanOrEqual(width);
      await page.screenshot({
        path: testInfo.outputPath(`connecting-${width}.png`),
        fullPage: true,
      });
    }
    releaseHealth();
    await expect(top.getByRole("status")).toHaveText("已连接");
    await expect(top.locator(".workspace-top-title")).toHaveText(
      `${project.split("/").at(-1)} · 帮助`,
    );
    releaseHelp();
    await expect(page.getByText("正在加载帮助…", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: /Open navigation$/ }).focus();
    await page.keyboard.press("Enter");
    const drawer = page.getByRole("dialog", { name: "工作区导航" });
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "数据 / Data", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(drawer).toHaveCount(0);
    await expect(top.locator(".workspace-top-title")).toContainText("· 数据");
    await expect(page).toHaveURL(/#history$/);
    unavailable = true;
    await refresh.focus();
    await page.keyboard.press("Enter");
    await expect(top.getByRole("status")).toHaveText("连接已断开");
    await expect(page.getByText(/请确认 Scopecat 正在运行，再刷新此页面/)).toBeVisible();
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() => page.evaluate<number>("document.documentElement.scrollWidth"))
        .toBeLessThanOrEqual(width);
      await page.screenshot({
        path: testInfo.outputPath(`disconnected-${width}.png`),
        fullPage: true,
      });
    }
    unavailable = false;
    status = "degraded";
    await refresh.click();
    await expect(top.getByRole("status")).toHaveText("服务受限");
    await expect(page.getByText("连接已中断。", { exact: true })).toHaveCount(0);
    status = "future_status";
    await refresh.click();
    await expect(top.getByRole("status")).toHaveText("状态未知（future_status）");
    status = undefined;
    await refresh.click();
    await expect(top.getByRole("status")).toHaveText("已连接");
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() => page.evaluate<number>("document.documentElement.scrollWidth"))
        .toBeLessThanOrEqual(width);
      await refresh.focus();
      await expect(refresh).toBeFocused();
      await page.screenshot({
        path: testInfo.outputPath(`reconnected-${width}.png`),
        fullPage: true,
      });
    }
    await expect(page).toHaveURL(/#history$/);
  } finally {
    releaseHealth();
    releaseHelp();
    if (started) stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
