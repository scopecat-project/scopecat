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
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
from ui_signal.analysis import PeakResult
with project.authoring() as author:
    content = initial_parameters()
    revision = author.parameters.save(name="repeat-inputs", catalog=content.catalog, parameters=content.parameters)
    author.parameters.create_branch("repeat-inputs", revision=revision)
    author.use(parameter_branch="repeat-inputs", setup=author.setup.get("initial"))
    run = author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}).run().wait().result()
    first = author.analyze_as(run.id, "ui_signal.analysis:estimate_peak", PeakResult)
    source = project.root / "src/ui_signal/analysis.py"
    source.write_text(source.read_text().replace("minimum_contrast: float = 0.2", "minimum_contrast: float = 2.0"))
    author.refresh()
    second = author.analyze_as(run.id, "ui_signal.analysis:estimate_peak", PeakResult, source="current")
    print(json.dumps({"run": run.id, "first": first.publication.id, "second": second.publication.id,
                     "source": first.publication.fact("author_code_revision").value}))
`;

test("repeats a retained ordinary analysis with its original source after author edits without acquisition", async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-analysis-repeat-"));
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
      run: string;
      first: string;
      second: string;
      source: string;
    };
    expect(seed.first).not.toBe(seed.second);
    let posts = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/author-revisions/analyze"))
        posts++;
    });
    await page.goto(
      `${endpoint.base_url}/?run=${encodeURIComponent(seed.run)}&run-analysis=${encodeURIComponent(seed.first)}#runs`,
    );
    const action = page.getByRole("region", { name: "Repeat saved analysis" });
    await expect(action.getByRole("button", { name: "Repeat with saved settings" })).toBeVisible();
    expect(posts).toBe(0);
    const pendingResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/author-revisions/analyze") &&
        response.request().method() === "POST",
    );
    await action.getByRole("button", { name: "Repeat with saved settings" }).click();
    const result = await pendingResponse;
    expect(result.status(), await result.text()).toBe(200);
    expect(result.request().postDataJSON()).toMatchObject({
      run_id: seed.run,
      code_revision: { content_hash: seed.source },
      arguments: {},
    });
    const receipt = await result.json();
    await expect(action.getByRole("link", { name: "View saved result" })).toHaveAttribute(
      "href",
      `?run=${encodeURIComponent(seed.run)}&run-analysis=${encodeURIComponent(receipt.analysis_id)}#runs`,
    );
    await action.screenshot({ path: testInfo.outputPath("repeat-saved-analysis.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await action.screenshot({ path: testInfo.outputPath("repeat-saved-analysis-narrow.png") });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    const saved = await page.request.get(
      `${endpoint.base_url}/api/v1/runs/${seed.run}/analyses/${receipt.analysis_id}`,
    );
    const publication = (await saved.json()).analysis;
    expect(
      publication.outputs.find((output: { id: string }) => output.id === "result").content.value
        .status,
    ).toBe("estimated");
    expect(
      publication.outputs.find((output: { id: string }) => output.id === "author_code_revision")
        .content.value,
    ).toBe(seed.source);
    await action.getByRole("link", { name: "View saved result" }).click();
    await expect(page.getByTestId("publication-id").first()).toHaveText(receipt.analysis_id);
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Repeat with saved settings" }).first(),
    ).toBeVisible();
    expect(posts).toBe(1);
    const runs = await page.request.get(`${endpoint.base_url}/api/v1/runs?limit=100`);
    expect((await runs.json()).items).toHaveLength(1);
    expect(
      (
        await page.request.get(
          `${endpoint.base_url}/api/v1/runs/${seed.run}/analyses/${seed.first}`,
        )
      ).status(),
    ).toBe(200);
    passed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved repeat project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
