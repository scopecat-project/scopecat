import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject, uv } from "./daemon";

const SUBMIT = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
with project.authoring() as session:
    session.refresh()
    from my_experiment.procedure import SurveyIntent, survey
    from my_experiment.setup import open_parameters
    from my_experiment.lesson_identity import IDENTITY
    params = open_parameters(session)
    saved = params.save(note="Synthetic chart binding browser acceptance")
    setup = session.setup.get(f"{IDENTITY}-setup")
    intent = SurveyIntent(config=session.parameters.resolve(saved, setup=setup), setup=setup.ref)
    task = session.procedures.prepare(survey, intent, request_key="browser-point-binding").submit()
    assert task.wait(timeout=45).state == "waiting_for_input"
    print(json.dumps({"procedure":task.id}))
`;

test("synthetic sweep point fills a draft, allows adjustment and explicitly continues", async ({
  page,
}, testInfo) => {
  test.setTimeout(180000);
  const root = await mkdtemp(join(tmpdir(), "scopecat-point-binding-"));
  const project = join(root, "lesson");
  let started = false;
  try {
    uv([
      "python",
      "-c",
      "import sys; sys.path.insert(0, sys.argv[2]); from testing.app_support.project import create_project; create_project(sys.argv[1], topic='procedure')",
      project,
      ROOT,
    ]);
    const base = await startProject(project);
    started = true;
    const { procedure } = JSON.parse(uv(["python", "-c", SUBMIT, project])) as {
      procedure: string;
    };
    await page.request.put(`${base}/api/v1/operator-settings`, {
      data: { operator: "Browser operator", revision: 0 },
    });
    await page.goto(`${base}/?procedure=${procedure}#decisions`);
    const judgment = page.getByLabel("Your judgment", { exact: true });
    const frequency = page.getByLabel("Peak frequency (MHz)", { exact: true });
    await expect(judgment).toBeVisible();
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await judgment.selectOption(JSON.stringify("Unsure"));
      await expect(frequency).toHaveCount(0);
      await judgment.selectOption(JSON.stringify("Peak selected"));
      const points = page
        .getByTestId("analysis-figure")
        .locator('svg path[stroke="#80a3cf"][fill="#fff"], svg path[fill="#facc15"]');
      await expect(points).toHaveCount(81);
      const selected = page.getByTestId("analysis-figure").locator('svg path[fill="#facc15"]');
      if (await selected.count()) await selected.click();
      else await points.nth(40).click();
      await page.mouse.move(0, 0);
      await expect(frequency).toHaveValue("6500");
      await expect(
        page.getByTestId("analysis-figure").locator('svg path[fill="#facc15"]'),
      ).toHaveCount(1);
      const state = await page.request.get(`${base}/api/v1/procedures/${procedure}`);
      expect((await state.json()).state).toBe("waiting_for_input");
      await frequency.fill("6500.25");
      await judgment.selectOption(JSON.stringify("No clear peak"));
      await expect(frequency).toHaveCount(0);
      await judgment.selectOption(JSON.stringify("Peak selected"));
      await expect(frequency).toHaveValue("6500.25");
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await page.evaluate(() => window.scrollTo(0, 0));
      await testInfo.attach(`selected-${width}`, {
        body: await page.screenshot({
          fullPage: true,
          path: testInfo.outputPath(`selected-${width}.png`),
        }),
        contentType: "image/png",
      });
    }
    await frequency.press("Enter");
    await expect(page.getByRole("link", { name: "Open task" })).toBeVisible();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`${base}/api/v1/procedures/${procedure}`)).json()).closure
            ?.status,
      )
      .toBe("succeeded");
    const steps = (
      await (
        await page.request.get(`${base}/api/v1/procedures/${procedure}/steps?limit=200`)
      ).json()
    ).items;
    const choice = steps.find((step: { step_key: string }) => step.step_key === "choose-1");
    expect(choice.output.response.value).toEqual({
      outcome: "Peak selected",
      frequency_mhz: 6500.25,
    });
    expect(choice.output.response.actor).toBe("Browser operator");
    expect(choice.output.response.actor_kind).toBe("human");
    expect((await (await page.request.get(`${base}/api/v1/runs`)).json()).items).toHaveLength(1);
  } finally {
    if (started) stopProject(project);
    await rm(root, { recursive: true, force: true });
  }
});
