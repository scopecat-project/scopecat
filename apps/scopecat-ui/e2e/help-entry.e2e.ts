import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject } from "./daemon";

test("prepared courses lead to the exact Notebook kernel and saved data", async ({ page }) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-help-"));
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const baseUrl = await startProject(project);
    await page.addInitScript(() => {
      Object.assign(window, {
        pywebview: {
          api: {
            set_window_title: async () => {},
            notebook_journey: async () => ({
              state: "ready",
              journey: {
                directory: "/test/lesson",
                notebook: "/test/lesson/notebooks/parameters.ipynb",
                python: "/test/lesson/.venv/bin/python",
                ready: true,
              },
            }),
            open_lesson_notebook: async () => {},
            prepare_notebook_journey: async () => {
              return {
                directory: "/test/lesson",
                notebook: "/test/lesson/notebooks/parameters.ipynb",
                python: "/test/lesson/.venv/bin/python",
                ready: true,
              };
            },
          },
        },
      });
    });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${baseUrl}/#help`);
      const resume = page.getByRole("button", {
        name: "Continue parameters Notebook",
        exact: true,
      });
      await expect(resume).toBeEnabled();
      expect((await resume.boundingBox())!.y).toBeLessThan(800);
      await resume.click();
      await expect(page.getByText("/test/lesson/.venv/bin/python", { exact: true })).toBeVisible();
      await expect(page.getByRole("alert")).toHaveCount(0);
      await expect(
        page.getByText("/test/lesson/notebooks/parameters.ipynb", { exact: true }),
      ).toHaveCount(0);
      if (process.env.SCOPECAT_SCREENSHOTS)
        await page.screenshot({
          path: `${process.env.SCOPECAT_SCREENSHOTS}/help-ready-${width}.png`,
          fullPage: true,
        });
      await page.getByRole("link", { name: "Find saved results in Data" }).click();
      await expect(page).toHaveURL(/#history$/);
      await expect(page.getByRole("heading", { name: "Data", exact: true })).toBeVisible();
    }
  } finally {
    stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
