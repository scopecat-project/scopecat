import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject } from "./daemon";

test("shared controls remain readable, keyboard accessible and contained across pages", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-controls-"));
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const baseUrl = await startProject(project);
    // Exercise desktop-only fields without invoking any native operation.
    await page.addInitScript(`(() => {
      Object.assign(window, {
        pywebview: {
          api: {
            status: async () => ({
              home: "/test/application",
              sources: [{ directory: "/test/author", python: "/test/notebook-python", execution_python: "/test/exec-python" }],
              installation: { python: "/test/python", environment: {} },
            }),
            set_window_title: async () => {},
            request_exit: async () => ({ runs: 1 }),
            notebook_journey: async () => ({ state: "not_started", journey: null }),
          },
        },
      });
    })()`);
    // Exercise an enum-array request through the real LaunchForm renderer.
    // This UI-only field is never submitted to acquisition.
    await page.route("**/api/v1/experiment-launcher", async (route) => {
      const response = await route.fetch();
      const catalog = await response.json();
      for (const entry of catalog.entries) {
        entry.request.properties.review_channels = {
          type: "array",
          title: "Review channels",
          items: { type: "string", enum: ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"] },
        };
      }
      await route.fulfill({ response, json: catalog });
    });
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [route, heading] of [
        ["history", "数据"],
        ["launch", "实验"],
        ["help", "Help"],
        ["settings", "Application settings"],
        ["configuration", "Experiment configuration"],
        ["instruments", "Devices and drivers"],
        ["samples", "Chips & samples"],
        ["analyses", "No project analyses saved"],
        ["runs", "No run selected"],
        ["reviews", "No live experiment reviews"],
        ["decisions", "当前没有等待人工判断的实验"],
      ]) {
        await page.goto(`${baseUrl}/#${route}`);
        await expect(
          page.getByRole("heading", { name: heading, exact: true }).first(),
        ).toBeVisible();
        if (route === "help") {
          const course = page.getByRole("combobox", { name: "Course", exact: true });
          await course.focus();
          await expect(course).toHaveCSS("outline-width", "2px");
          const start = page.getByRole("button", {
            name: "Start parameters Notebook",
            exact: true,
          });
          await expect(start).toBeEnabled();
          expect((await start.boundingBox())!.y).toBeLessThan(800);
          await course.selectOption("groups");
          await expect(
            page.getByRole("button", { name: "Start groups Notebook", exact: true }),
          ).toBeEnabled();
          await course.selectOption("parameters");
        }
        if (route === "configuration") {
          const operator = page.getByLabel("Operator", { exact: true });
          await operator.focus();
          await expect(operator).toHaveCSS("font-size", "14px");
          await expect(operator).toHaveCSS("outline-width", "2px");
          expect((await operator.boundingBox())!.height).toBeGreaterThanOrEqual(36);
        }
        if (route === "instruments") {
          const add = page.getByRole("button", { name: "Add device", exact: true });
          // This source has no driver package; the dedicated virtual-device journey
          // checks the enabled dialog and its focus return.
          await expect(add).toBeDisabled();
        }
        if (route === "launch") {
          await expect(page.getByLabel("实验类型", { exact: true })).toBeVisible({
            timeout: 60_000,
          });
          const channels = page.getByRole("listbox", { name: "Review channels", exact: true });
          await expect(channels).toBeVisible();
          expect((await channels.boundingBox())!.height).toBeGreaterThanOrEqual(128);
          await expect(channels).toHaveAttribute("size", "5");
          const review = page.getByRole("button", { name: "确认已核实输入", exact: true });
          if (await review.isVisible()) await review.click();
          await expect(channels).toBeEnabled();
          await channels.selectOption(["alpha", "gamma"]);
          await expect(channels).toHaveValues(["alpha", "gamma"]);
          await channels.focus();
          await expect(channels).toHaveCSS("outline-width", "2px");
        }
        if (route === "settings") {
          const input = page.getByLabel("Author directory", { exact: true });
          await expect(input).toBeVisible();
          await input.focus();
          await page.keyboard.press("Tab");
          const next = page.getByLabel("Execution Python", { exact: true });
          await expect(next).toBeFocused();
          await expect(next).toHaveCSS("outline-width", "2px");
          await expect(
            page.getByRole("button", { name: "Add code folder", exact: true }),
          ).toBeDisabled();
          expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(36);
          await page
            .getByRole("combobox", { name: "Your code folders", exact: true })
            .selectOption("/test/author");
          await expect(page.getByText("/test/exec-python", { exact: true })).not.toBeVisible();
          await page.getByText("Inspect or change Python environments", { exact: true }).click();
          await expect(page.getByText("/test/exec-python", { exact: true })).toBeVisible();
          await page.getByRole("button", { name: "New code folder", exact: true }).click();
          await expect(page.getByLabel("New folder name", { exact: true })).toBeVisible();
          await expect(page.getByLabel("Execution Python", { exact: true })).toHaveCount(0);
          await expect(page.getByText("/test/exec-python", { exact: true })).toHaveCount(0);
        }
        if (route === "history") {
          const select = page.getByLabel("研究项目", { exact: true });
          await select.focus();
          await expect(select).toHaveCSS("outline-width", "2px");
          await page.keyboard.press("Tab");
          await expect(page.getByLabel("Sample", { exact: true })).toBeFocused();
          const lookup = page.getByLabel("完整 run ID", { exact: true });
          if (!(await lookup.isVisible()))
            await page.getByText("按完整 run ID 查找", { exact: true }).click();
          await lookup.fill("invalid/path");
          await page.getByRole("button", { name: "查找精确 run", exact: true }).click();
          await expect(lookup).toHaveAttribute("aria-invalid", "true");
          await expect(lookup).toHaveCSS("border-top-color", "rgb(167, 68, 54)");
          await expect(
            page.getByRole("alert").filter({ hasText: "run ID 格式无效" }),
          ).toBeVisible();
        }
        expect(
          await page.evaluate<number>("document.documentElement.scrollWidth"),
        ).toBeLessThanOrEqual(width);
        await page.evaluate("window.scrollTo(0, 0)");
        await testInfo.attach(`${route}-${width}`, {
          body: await page.screenshot({
            fullPage: true,
            ...(process.env.SCOPECAT_SCREENSHOTS
              ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/${route}-${width}.png` }
              : {}),
          }),
          contentType: "image/png",
        });
      }
    }
    await page.keyboard.press("Control+q");
    await expect(page.getByRole("dialog", { name: "Quit Scopecat" })).toBeVisible();
    await testInfo.attach("quit-dialog-390", {
      body: await page.screenshot({
        fullPage: true,
        ...(process.env.SCOPECAT_SCREENSHOTS
          ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/quit-dialog-390.png` }
          : {}),
      }),
      contentType: "image/png",
    });
    await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      // The application currently supports a light theme under both OS preferences.
      await expect(page.locator("html")).toHaveCSS("color-scheme", "light");
    }
  } finally {
    stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
