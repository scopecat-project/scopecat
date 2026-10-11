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
function retainedWorkers(project: string): number[] {
  return JSON.parse(
    uv([
      "python",
      "-c",
      `
import json, psutil, sys
print(json.dumps(sorted(p.pid for p in psutil.process_iter(["cmdline"])
    if "scopecat_server.retained_worker" in (p.info["cmdline"] or [])
    and sys.argv[1] in (p.info["cmdline"] or []))))
`,
      project,
    ]),
  );
}
const SEED = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
with project.authoring() as author:
    content = initial_parameters()
    revision = author.parameters.save(name="action-inputs", catalog=content.catalog, parameters=content.parameters)
    author.parameters.create_branch("action-inputs", revision=revision)
    author.use(parameter_branch="action-inputs", setup=author.setup.get("initial"))
    run = author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}).run().wait().result()
    source = project.root / "src/ui_signal/analysis.py"
    source.write_text(source.read_text().replace("minimum_contrast: float = 0.2", "minimum_contrast: float = 2.0"))
    declaration = project.root / "src/ui_signal/ordinary.py"
    declaration.write_text(declaration.read_text().replace('"Estimate peak"', '"Changed peak action"'))
    author.refresh()
    declaration.write_text(declaration.read_text().replace('"Changed peak action": "ui_signal.analysis:estimate_peak",', '').replace('"Check expected peak": "ui_signal.analysis:verify_peak",', ''))
    author.refresh()
    old_run = author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}).run().wait().result()
    print(json.dumps({"run": run.id, "old_run": old_run.id, "source": run.request.metadata["author_code_revision"],
                     "workspace": run.request.metadata["author_workspace"]}))
`;

test("starts the declared first analysis from a retained result after current source edits", async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-analysis-actions-"));
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
      old_run: string;
      source: string;
      workspace: string;
    };
    let posts = 0;
    let directoryReads = 0;
    page.on("request", (request) => {
      if (request.url().endsWith("/analysis-actions")) directoryReads++;
      if (request.method() === "POST" && request.url().endsWith("/author-revisions/analyze"))
        posts++;
    });
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(seed.run)}#runs`);
    await expect(page.locator("summary").filter({ hasText: "Analyze this result" })).toBeVisible();
    expect(directoryReads).toBe(0);
    expect(retainedWorkers(project)).toEqual([]);
    await page.locator("summary").filter({ hasText: "Analyze this result" }).click();
    const actions = page.getByRole("region", { name: "Analyze this result" });
    await expect(actions.getByRole("button", { name: "Run Estimate peak" })).toBeVisible();
    expect(posts).toBe(0);
    expect(directoryReads).toBe(1);
    const workers = retainedWorkers(project);
    expect(workers).toHaveLength(1);
    await expect(actions.getByLabel("Minimum Contrast")).toHaveValue("0.2");
    await expect(page.getByRole("button", { name: "Run Changed peak action" })).toHaveCount(0);
    await actions.screenshot({ path: testInfo.outputPath("declared-analyses-wide.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await actions.screenshot({ path: testInfo.outputPath("declared-analyses-narrow.png") });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    const fitResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/author-revisions/analyze") &&
        response.request().method() === "POST",
    );
    await actions.getByRole("button", { name: "Run Estimate peak" }).click();
    const fit = await fitResponse;
    expect(fit.status(), await fit.text()).toBe(200);
    expect(fit.request().postDataJSON()).toMatchObject({
      run_id: seed.run,
      workspace_id: seed.workspace,
      code_revision: { content_hash: seed.source },
      arguments: { minimum_contrast: 0.2 },
    });
    const fitReceipt = await fit.json();
    const estimate = actions.getByRole("form", { name: "Estimate peak" });
    await estimate.getByRole("link", { name: "View saved result" }).click();
    await expect(page.getByTestId("publication-id").first()).toHaveText(fitReceipt.analysis_id);
    const fitSaved = await page.request.get(
      `${endpoint.base_url}/api/v1/runs/${seed.run}/analyses/${fitReceipt.analysis_id}`,
    );
    expect(
      (await fitSaved.json()).analysis.outputs.find((item: { id: string }) => item.id === "result")
        .content.value.status,
    ).toBe("estimated");
    await page.locator("summary").filter({ hasText: "Analyze this result" }).click();
    await expect(actions.getByRole("button", { name: "Run Check expected peak" })).toBeVisible();
    expect(retainedWorkers(project)).toEqual(workers);
    await actions.getByLabel("Expected Frequency", { exact: true }).fill("4.8");
    await actions.getByLabel("Expected Frequency unit").fill("GHz");
    const checkResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/author-revisions/analyze") &&
        response.request().method() === "POST",
    );
    await actions.getByRole("button", { name: "Run Check expected peak" }).click();
    const check = await checkResponse;
    expect(check.status(), await check.text()).toBe(200);
    expect(check.request().postDataJSON()).toMatchObject({
      code_revision: { content_hash: seed.source },
      arguments: {
        expected_frequency: { value: 4.8, unit: "GHz" },
        tolerance: { value: 50, unit: "MHz" },
      },
    });
    const checkReceipt = await check.json();
    const checkSaved = await page.request.get(
      `${endpoint.base_url}/api/v1/runs/${seed.run}/analyses/${checkReceipt.analysis_id}`,
    );
    expect(
      (await checkSaved.json()).analysis.outputs.find(
        (item: { id: string }) => item.id === "result",
      ).content.value.accepted,
    ).toBe(true);
    const readsBeforeReload = directoryReads;
    await page.reload();
    await expect(page.getByTestId("publication-id").first()).toHaveText(fitReceipt.analysis_id);
    await expect(page.locator("summary").filter({ hasText: "Analyze this result" })).toBeVisible();
    expect(directoryReads).toBe(readsBeforeReload);
    expect(retainedWorkers(project)).toEqual(workers);
    expect(posts).toBe(2);
    const runs = await page.request.get(`${endpoint.base_url}/api/v1/runs?limit=100`);
    expect((await runs.json()).items).toHaveLength(2);
    expect(
      (
        await page.request.get(
          `${endpoint.base_url}/api/v1/runs/${seed.run}/analyses/${fitReceipt.analysis_id}`,
        )
      ).status(),
    ).toBe(200);
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(seed.old_run)}#runs`);
    const expand = page.locator("summary").filter({ hasText: "Analyze this result" });
    await expect(expand).toBeVisible();
    expect(directoryReads).toBe(readsBeforeReload);
    expect(retainedWorkers(project)).toEqual(workers);
    await expand.click();
    await expect(page.getByText(/No analyses were declared/)).toBeVisible();
    expect(directoryReads).toBe(readsBeforeReload + 1);
    expect(retainedWorkers(project)).toHaveLength(2);
    expect(posts).toBe(2);
    passed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved action project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
