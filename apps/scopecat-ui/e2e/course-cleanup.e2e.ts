import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject } from "./daemon";

test("course Data and explicit cleanup remain readable and keyboard accessible", async ({
  page,
}) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-course-ui-"));
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const baseUrl = await startProject(project);
    const selection = {
      runs: [],
      procedures: [],
      analyses: [],
      parameters: ["course-parameters"],
      setups: [],
      setup_definitions: [],
      branches: [],
      plans: [],
      schedules: [],
      calibration_tasks: [],
      calibration_profiles: [],
      captures: [],
    };
    await page.route("**/api/v1/practice/course-ui", (route) =>
      route.fulfill({ json: { id: "course-ui", lesson: "parameters", state: "active" } }),
    );
    await page.route("**/api/v1/notebook-practice/course-ui/preview", (route) =>
      route.fulfill({
        json: {
          selection,
          record_count: 1,
          blockers: [],
          fingerprint: "exact",
          bytes_to_reclaim: 0,
        },
      }),
    );
    await page.addInitScript(
      ({ selection }) => {
        let cleared = false;
        let previews = 0;
        const journey = () => ({
          directory: "/authors/edited-parameters-course",
          notebook: "/authors/edited-parameters-course/notebooks/parameters.ipynb",
          python: "/authors/edited-parameters-course/.venv/bin/python",
          ready: !cleared,
          scope_id: "course-ui",
          cleared,
        });
        Object.assign(globalThis, {
          pywebview: {
            api: {
              set_window_title: async () => {},
              notebook_journey: async () => ({
                state: cleared ? "retryable" : "ready",
                journey: journey(),
              }),
              preview_notebook_cleanup: async () => ({
                files_error: null,
                preview: {
                  selection,
                  record_count: 1,
                  fingerprint: "exact",
                  bytes_to_reclaim: 0,
                  blockers:
                    ++previews === 1
                      ? [
                          {
                            owner: "procedure:waiting",
                            reason: "Finish or cancel this task before cleanup",
                          },
                        ]
                      : [],
                },
              }),
              clear_notebook_course: async (_topic: string, raw: string) => {
                if (JSON.parse(raw).files !== "preserve")
                  throw new Error("Code must be preserved by default");
                cleared = true;
                return journey();
              },
            },
          },
        });
      },
      { selection },
    );
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 950 });
      await page.goto(`${baseUrl}/#help`);
      await page.getByRole("link", { name: "View this course in Data" }).click();
      await expect(page.getByRole("heading", { name: "Course: parameters" })).toBeVisible();
      await expect(page).toHaveURL(/course=course-ui#history/);
      if (process.env.SCOPECAT_SCREENSHOTS)
        await page.screenshot({
          path: `${process.env.SCOPECAT_SCREENSHOTS}/course-data-${width}.png`,
          fullPage: true,
        });
      await page.goto(`${baseUrl}/#help`);
      await page.getByText("Clear this course", { exact: true }).click();
      const preview = page.getByRole("button", { name: "Preview course cleanup" });
      await preview.click();
      const confirm = page.getByRole("button", { name: "Confirm course cleanup" });
      await expect(confirm).toBeDisabled();
      await preview.click();
      await expect(confirm).toBeEnabled();
      await expect(
        page.getByRole("checkbox", { name: /Also delete this course/ }),
      ).not.toBeChecked();
      await page.getByRole("checkbox", { name: /Also delete this course/ }).focus();
      await page.keyboard.press("Tab");
      await expect(confirm).toBeFocused();
      expect(
        await confirm.evaluate((el) => {
          const style = el.ownerDocument.defaultView!.getComputedStyle(el);
          return style.boxShadow !== "none" || style.outlineStyle !== "none";
        }),
      ).toBe(true);
      expect(
        await page
          .locator("html")
          .evaluate((el) => el.scrollWidth <= el.ownerDocument.defaultView!.innerWidth),
      ).toBe(true);
      if (process.env.SCOPECAT_SCREENSHOTS)
        await page.screenshot({
          path: `${process.env.SCOPECAT_SCREENSHOTS}/course-cleanup-${width}.png`,
          fullPage: true,
        });
      await page.keyboard.press("Enter");
      await expect(
        page.getByText("Course data cleared. Prepare again to start a fresh course folder."),
      ).toBeVisible();
    }
  } finally {
    stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
