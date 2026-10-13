import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 60_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(`${result.error?.message ?? ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout.trim();
}
const SEED = `
import json, sys
import scopecat as sc
from scopecat.records.sample import SampleRevisionDraft
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
with project.connect() as lab, project.authoring() as author:
    lab.samples.create("related-chip", kind="synthetic", content=SampleRevisionDraft(display_name="Related chip"))
    content = initial_parameters()
    revision = author.parameters.save(name="related-inputs", catalog=content.catalog, parameters=content.parameters)
    author.parameters.create_branch("related-inputs", revision=revision)
    author.use(parameter_branch="related-inputs", setup=author.setup.get("initial"), sample="related-chip")
    runs = [author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}).run().wait().result() for _ in range(2)]
    a, b = [lab.get_run(run.id) for run in runs]
    comparison = a.analysis("Pair comparison", key="pair")
    comparison.measurements(id="primary")
    comparison.measurements(b, id="secondary")
    result = comparison.result().fact("score", 1)
    first = result.save()
    assert result.save().id == first.id
    revised = comparison.result().fact("score", 2).save()
    sample = lab.analysis("Chip summary", key="summary", sample="related-chip")
    sample.measurements(a, id="first")
    sample.measurements(b, id="second")
    summary = sample.result().fact("score", 3).save()
    space = lab.analysis("Space summary", key="summary")
    space.measurements(a, id="first")
    space.measurements(b, id="second")
    space_result = space.result().fact("score", 4).save()
    print(json.dumps({"runs": [a.id, b.id], "ids": [first.id, revised.id, summary.id, space_result.id]}))
`;

test("finds the same run, sample and space publications from either direct input", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-related-analyses-"));
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const seed = JSON.parse(uv(["python", "-c", SEED, project])) as {
      runs: string[];
      ids: string[];
    };
    for (const run of seed.runs) {
      await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(run)}#runs`);
      const related = page.getByRole("region", { name: "Related analyses", exact: true });
      await expect(related.locator(":scope > details > summary")).toHaveCount(4);
      for (const summary of await related.locator(":scope > details > summary").all())
        await summary.click();
      await expect(related.getByTestId("publication-id")).toHaveCount(4);
      expect((await related.getByTestId("publication-id").allTextContents()).sort()).toEqual(
        [...seed.ids].sort(),
      );
      await related.getByText("Sources and execution evidence", { exact: true }).first().click();
      for (const input of seed.runs)
        await expect(related.getByRole("button", { name: input, exact: true })).toBeVisible();
      await expect(related.getByText(/Sample Related chip/)).toBeVisible();
      await expect(related.getByText(/Data space · revision/)).toBeVisible();
    }
    await page.evaluate("window.__relatedJourneyContext = 'retained'");
    await page
      .getByRole("region", { name: "Related analyses", exact: true })
      .getByRole("button", { name: seed.runs[0], exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[0]}`));
    expect(await page.evaluate("window.__relatedJourneyContext")).toBe("retained");
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[1]}`));
    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[0]}`));
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    passed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
  }
});
