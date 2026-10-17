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
              sources: [],
              installation: { python: "/test/python", environment: {} },
            }),
            set_window_title: async () => {},
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
        ["history", "Data"],
        ["launch", "Experiments"],
        ["help", "Help"],
        ["settings", "Application settings"],
      ]) {
        await page.goto(`${baseUrl}/#${route}`);
        await expect(
          page.getByRole("heading", { name: heading, exact: true }).first(),
        ).toBeVisible();
        if (route === "launch") {
          await expect(page.getByLabel("Experiment", { exact: true })).toBeVisible({
            timeout: 60_000,
          });
          const channels = page.getByRole("listbox", { name: "Review channels", exact: true });
          await expect(channels).toBeVisible();
          expect((await channels.boundingBox())!.height).toBeGreaterThanOrEqual(128);
          await expect(channels).toHaveAttribute("size", "5");
          const review = page.getByRole("button", { name: "Confirm reviewed input", exact: true });
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
        }
        if (route === "history") {
          const select = page.getByLabel("Research project", { exact: true });
          await select.focus();
          await expect(select).toHaveCSS("outline-width", "2px");
          await page.keyboard.press("Tab");
          await expect(page.getByLabel("Sample", { exact: true })).toBeFocused();
          const lookup = page.getByLabel("Full run ID", { exact: true });
          if (!(await lookup.isVisible()))
            await page.getByText("Find by full run ID", { exact: true }).click();
          await lookup.fill("invalid/path");
          await page.getByRole("button", { name: "Find exact run", exact: true }).click();
          await expect(lookup).toHaveAttribute("aria-invalid", "true");
          await expect(lookup).toHaveCSS("border-top-color", "rgb(215, 126, 121)");
          await expect(
            page.getByRole("alert").filter({ hasText: "Invalid run ID format" }),
          ).toBeVisible();
        }
        expect(
          await page.evaluate<number>("document.documentElement.scrollWidth"),
        ).toBeLessThanOrEqual(width);
        await testInfo.attach(`${route}-${width}`, {
          body: await page.screenshot({ fullPage: true }),
          contentType: "image/png",
        });
      }
    }
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      // The application currently supports a dark theme under both OS preferences.
      await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
    }
  } finally {
    stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
