import { spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import type { components } from "../src/api-schema";

const ROOT = resolve(process.cwd(), "../..");
function python(script: string, ...args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync(
    "uv",
    ["run", "--locked", "--project", ROOT, "python", "-c", script, ...args],
    {
      env,
      encoding: "utf8",
      timeout: 90_000,
    },
  );
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
  return result.stdout.trim().split("\n").at(-1)!;
}

test("ordinary author loop recovers a lost submission without collecting again", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  const directory = await mkdtemp(join(tmpdir(), "scopecat-application-loop-"));
  const home = join(directory, "application");
  try {
    const prepared = JSON.parse(
      python(
        `
import json, sys
from pathlib import Path
import scopecat as sc
from lab_tools.application_runtime import ApplicationRuntime
from scopecat_server.scaffold import write_author_scaffold
runtime = ApplicationRuntime(Path(sys.argv[1]))
source = Path(sys.argv[2])
write_author_scaffold(source)
runtime.configure(static_dir=Path(sys.argv[3]))
workspace = runtime.register_source(source)
record = runtime.start()
project = sc.open_project(source)
assert project.runtime_binding.data_root == runtime.root / ".scopecat"
_ = project.load_application()
with project.authoring() as session:
    from scopecat_lab.authored.parameters import open_parameters
    from scopecat_lab.configuration import initial_setup
    open_parameters(session)
    resolved = session.setup.import_recipe(initial_setup(), name="loop-template")
    definition = session.setup.definition(resolved.resolution.definition_id).definition
    session.setup.save(definition, name="loop-bench")
print(json.dumps({"url": record.base_url, "workspace": workspace}))
`,
        home,
        join(directory, "editable-author"),
        resolve("dist"),
      ),
    ) as { url: string; workspace: string };
    await page.goto(`${prepared.url}/?workspace=${prepared.workspace}#configuration`);
    await page.getByLabel("Working parameter branch", { exact: true }).fill("starter");
    await page.getByRole("button", { name: "Open working table", exact: true }).click();
    const scale = page.getByLabel("response[1].scale", { exact: true });
    await expect(scale).toBeVisible();
    await scale.fill("2");
    await page
      .getByRole("button", { name: "Use working inputs for next experiment", exact: true })
      .click();
    await page.getByLabel("Experiment setup", { exact: true }).selectOption("loop-bench");
    await page.getByLabel("Position", { exact: true }).fill("0.5");
    const start = page.getByRole("button", { name: "Start acquisition", exact: true });
    await expect(start).toBeEnabled();
    let submissions = 0;
    let release!: () => void;
    const held = new Promise<void>((done) => {
      release = done;
    });
    let admitted!: () => void;
    const receiptReady = new Promise<void>((done) => {
      admitted = done;
    });
    let procedure = "";
    await page.route("**/experiment-launcher/submit", async (route) => {
      submissions += 1;
      const response = await route.fetch();
      expect(response.status(), await response.text()).toBe(200);
      procedure = (await response.json()).procedure_id;
      admitted();
      await held;
      await route.abort("connectionfailed");
    });
    await start.click();
    await receiptReady;
    await expect(page.getByRole("button", { name: "Submitting acquisition…" })).toBeDisabled();
    const pendingBounds = (await page
      .getByRole("button", { name: "Submitting acquisition…" })
      .boundingBox())!;
    await page.mouse.click(pendingBounds.x + 5, pendingBounds.y + 5);
    expect(submissions).toBe(1);
    await page.getByRole("button", { name: "Configuration", exact: true }).click();
    release();
    await expect(scale).toHaveValue("2");
    await scale.fill("3");
    await expect(page.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Experiments", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Original submission awaiting confirmation" }),
    ).toBeVisible();
    await expect(start).toBeDisabled();
    await page.getByRole("button", { name: "Check original submission", exact: true }).click();
    await page.getByRole("button", { name: "Open submitted procedure", exact: true }).click();
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    // This author-linked URL still carries workspace context. Reopening the
    // procedure changes its query and must not reload the window's draft.
    expect(new URL(page.url()).searchParams.get("workspace")).toBe(prepared.workspace);
    await page.getByRole("link", { name: "Reopen this procedure", exact: true }).click();
    await expect(page.getByLabel("Position", { exact: true })).toHaveValue("0.5");
    const result = page.getByRole("link", { name: /^Open retained run:/ });
    const href = (await result.getAttribute("href"))!;
    const run = new URL(href, page.url()).searchParams.get("run")!;
    await result.click();
    await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
    await page.goBack();
    await expect(page.getByLabel("Position", { exact: true })).toHaveValue("0.5");
    await page.goForward();
    await expect(page.getByTitle(run, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Experiments", exact: true }).click();
    await page.getByRole("link", { name: "Reopen this procedure", exact: true }).click();
    await expect(page.getByLabel("Position", { exact: true })).toHaveValue("0.5");
    await expect(page.getByText(/Using working input revision/)).toBeVisible();
    await page.getByRole("button", { name: "Configuration", exact: true }).click();
    await expect(scale).toHaveValue("3");
    await scale.fill("4");
    await expect(page.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    expect(submissions).toBe(1);
    const runs = (await (
      await page.request.get(`${prepared.url}/api/v1/runs`)
    ).json()) as components["schemas"]["RunSummaryPage"];
    expect(runs.items.map((item) => item.control.admission.run_id)).toEqual([run]);
    const evidence = python(
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    run = lab.get_run(sys.argv[2])
    assert run.config.parameter_snapshot.get("response").rows[0]["scale"] == 2
    assert list(run.measurements()["result"].require_values()) == [1.6]
    assert lab.parameters.checkout("starter").head.generation == 1
    print(run.id)
`,
      join(directory, "editable-author"),
      run,
    );
    expect(evidence).toBe(run);
    await testInfo.attach("ordinary-application-loop", {
      body: JSON.stringify({ workspace: prepared.workspace, procedure, run, submissions }),
      contentType: "application/json",
    });
    await page.screenshot({ path: testInfo.outputPath("continued-working-table.png") });
  } finally {
    python(
      `from pathlib import Path
import sys
from lab_tools.application_runtime import ApplicationRuntime
ApplicationRuntime(Path(sys.argv[1])).stop()`,
      home,
    );
    await rm(directory, { recursive: true, force: true });
  }
});
