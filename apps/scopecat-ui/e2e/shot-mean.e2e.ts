import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type TestInfo } from "@playwright/test";
import { ROOT, uv, startProject, stopProject } from "./daemon";

test("explicit shot arrays default to means while retaining raw traces", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-shot-mean-"));
  for (const name of ["src", "scopecat.toml"])
    await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
      recursive: true,
    });
  await cp("e2e/fixtures/shot-signal.py", join(project, "src/ui_signal/shots.py"));
  const app = join(project, "src/ui_signal/application.py");
  const original = await readFile(app, "utf8");
  await writeFile(
    app,
    original.replace(
      'author_modules=("ui_signal.signal",',
      'author_modules=("ui_signal.shots", "ui_signal.signal",',
    ),
  );
  let passed = false;
  try {
    const url = await startProject(project);
    const run = JSON.parse(
      uv([
        "python",
        "-c",
        `
import json, sys
import numpy as np
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.shots import signal, AMPLITUDE
from ui_signal.application import save_inputs
with project.connect() as lab:
    inputs = save_inputs(lab)
    plan = signal.build().with_axis(sc.axis(AMPLITUDE, [0., .1, .2, .3, .4, .5, .6]))
    run = lab.run(plan, config=inputs)
    assert np.asarray(run.measurements()["iq"].require_values()).shape == (7, 64)
    print(json.dumps(run.id))
`,
        project,
      ]),
    ) as string;
    await page.goto(`${url}/?run=${encodeURIComponent(run)}#runs`);
    const mean = page.getByRole("img", { name: /shot 平均/ });
    await expect(mean).toBeVisible();
    await expect(page.getByText(/每点 64 shots/)).toBeVisible();
    const raw = page.getByText("原始轨迹", { exact: true });
    await expect(raw.locator("..")).not.toHaveAttribute("open", "");
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.evaluate("window.scrollTo(0, 0)");
      expect(await page.evaluate("document.documentElement.scrollWidth")).toBeLessThanOrEqual(
        width,
      );
      await testInfo.attach(`shot-mean-${width}`, {
        body: await page.screenshot({
          fullPage: true,
          ...(process.env.SCOPECAT_SCREENSHOTS
            ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/shot-mean-${width}.png` }
            : {}),
        }),
        contentType: "image/png",
      });
    }
    await raw.click();
    await expect(page.getByLabel("Measurement trace")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("img", { name: /shot 平均/ })).toBeVisible();
    passed = true;
  } finally {
    await cleanupProject(project, passed, testInfo);
    await rm(project, { recursive: true, force: true });
  }
});

async function cleanupProject(project: string, passed: boolean, testInfo: TestInfo) {
  try {
    stopProject(project);
  } catch (error) {
    if (passed) throw error;
    await testInfo.attach("cleanup failure", { body: String(error), contentType: "text/plain" });
  }
}
