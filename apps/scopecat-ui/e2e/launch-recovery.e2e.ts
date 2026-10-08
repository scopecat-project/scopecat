import { spawnSync } from "node:child_process";
import { mkdtemp, rm, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

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

test("raw experiment input, conflicts and exact original submission survive restart", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const directory = await mkdtemp(join(tmpdir(), "scopecat-launch-recovery-"));
  const home = join(directory, "application");
  const restart = () =>
    python(
      `from pathlib import Path
import sys
from lab_tools.application_runtime import ApplicationRuntime
runtime = ApplicationRuntime(Path(sys.argv[1]))
runtime.stop()
print(runtime.start().base_url)`,
      home,
    );
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

    let url = prepared.url;
    await page.goto(`${url}/?workspace=${prepared.workspace}#configuration`);
    await page.getByLabel("Working parameter branch", { exact: true }).fill("starter");
    await page.getByRole("button", { name: "Open working table", exact: true }).click();
    await page
      .getByRole("button", { name: "Use working inputs for next experiment", exact: true })
      .click();
    await page.getByLabel("Experiment setup", { exact: true }).selectOption("loop-bench");
    await page.getByLabel("Position source", { exact: true }).selectOption("values");
    await page.getByLabel("Position scan values", { exact: true }).fill("-1, nope, 1");
    await page.getByLabel("Center", { exact: true }).fill("-");
    const saved = (target = page) =>
      expect(
        target.getByText("Experiment input saved in application data.", { exact: true }),
      ).toBeVisible();
    await saved();
    url = restart();
    await page.goto(`${url}/?workspace=${prepared.workspace}#launch`);
    await expect(page.getByLabel("Position scan values", { exact: true })).toHaveValue(
      "-1, nope, 1",
    );
    await expect(page.getByLabel("Center", { exact: true })).toHaveValue("-");
    await expect(page.getByRole("button", { name: "Preview", exact: true })).toBeDisabled();
    await saved();
    const other = await context.newPage();
    await other.goto(`${url}/?workspace=${prepared.workspace}#launch`);
    await expect(other.getByLabel("Center", { exact: true })).toHaveValue("-");
    await page.getByLabel("Center", { exact: true }).fill("0.25");
    await saved();
    await other.getByLabel("Center", { exact: true }).fill("invalid in second window");
    await expect(other.getByRole("region", { name: "Conflicting experiment input" })).toBeVisible();
    await other.reload();
    await expect(other.getByLabel("Center", { exact: true })).toHaveValue("0.25");
    await other
      .getByText("Recover experiment input or an original submission", { exact: true })
      .click();
    await expect(other.getByText(/signal.*conflict/)).toBeVisible();
    await other.close();
    const source = join(
      directory,
      "editable-author",
      "src",
      "scopecat_lab",
      "authored",
      "signal.py",
    );
    // The implementation/default refresh must retain the edited scan and raw input.
    const original = await readFile(source, "utf8");
    await writeFile(source, original.replace("] = 0.0,", "] = 0.5,"));
    await page.getByRole("button", { name: /Refresh.*code/i }).click();
    await expect(page.getByLabel("Position scan values", { exact: true })).toHaveValue(
      "-1, nope, 1",
    );
    await expect(page.getByLabel("Center", { exact: true })).toHaveValue("0.25");
    await page.getByLabel("Position scan values", { exact: true }).fill("-1, 0, 1");
    await page.getByLabel("Center", { exact: true }).fill("0");
    await page.getByRole("button", { name: "Confirm reviewed input", exact: true }).click();
    await saved();
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    const start = page.getByRole("button", { name: "Start acquisition", exact: true });
    await expect(start).toBeEnabled();
    let submissions = 0;
    let procedure = "";
    await page.route("**/experiment-launcher/submit", async (route) => {
      submissions++;
      const attempts = await (await page.request.get(`${url}/api/v1/launch-attempts`)).json();
      expect(attempts.items).toHaveLength(1);
      expect(attempts.items[0].request).toMatchObject(route.request().postDataJSON());
      const response = await route.fetch();
      expect(response.status(), await response.text()).toBe(200);
      procedure = (await response.json()).procedure_id;
      await route.abort("connectionfailed");
    });
    await start.click();
    await expect(
      page.getByRole("button", { name: "Check original submission", exact: true }),
    ).toBeVisible();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`${url}/api/v1/procedures/${procedure}/operator`)).json())
            .procedure.state,
      )
      .toBe("closed");
    url = restart();
    await page.goto(`${url}/?workspace=${prepared.workspace}#launch`);
    await page.getByRole("button", { name: "Check original submission", exact: true }).click();
    await page.getByRole("button", { name: "Open submitted procedure", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`procedure=${procedure}`));
    expect(submissions).toBe(1);
    const runs = await (await page.request.get(`${url}/api/v1/runs`)).json();
    expect(runs.items).toHaveLength(1);
    await page
      .getByRole("button", { name: "Prepare a new run (separate acquisition)", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    expect((await (await page.request.get(`${url}/api/v1/runs`)).json()).items).toHaveLength(1);
    await testInfo.attach("recovery-evidence", {
      body: JSON.stringify({
        workspace: prepared.workspace,
        procedure,
        submissions,
        run: runs.items[0].control.admission.run_id,
      }),
      contentType: "application/json",
    });
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
