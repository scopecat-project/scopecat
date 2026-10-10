import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { copyAuthorWorkspace, prepareAuthorContexts } from "./author-context";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]): string {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 30_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
  return result.stdout.trim();
}

async function start(project: string) {
  await copyAuthorWorkspace(project);
  uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
  prepareAuthorContexts(uv, project);
  return (
    JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    }
  ).base_url;
}

async function editVersion(page: Page, name: string) {
  await page
    .getByRole("navigation", { name: "Project sections" })
    .getByRole("button", { name: "Configuration", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Saved parameter version", exact: true })
    .selectOption(name);
  await page.getByRole("button", { name: "Edit a copy", exact: true }).click();
}

async function saveVersion(page: Page, name: string) {
  await page.getByText("Save a parameter checkpoint", { exact: true }).click();
  await page.getByLabel("New version name", { exact: true }).fill(name);
  const response = page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname.endsWith("/commit") && item.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save parameter version", exact: true }).click();
  const saved = await response;
  expect(saved.status(), await saved.text()).toBe(200);
  await expect(
    page.getByRole("combobox", { name: "Saved parameter version", exact: true }),
  ).toHaveValue(name);
}

async function launchVersion(page: Page) {
  await page.getByRole("button", { name: "Use for next experiment", exact: true }).click();
  await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
  await page.getByLabel("Experiment setup", { exact: true }).selectOption("browser-bench-a");
}

async function acquire(page: Page): Promise<string> {
  const submitting = page.waitForResponse(
    (response) =>
      response.url().endsWith("/experiment-launcher/submit") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
  const response = await submitting;
  expect(response.status(), await response.text()).toBe(200);
  const receipt = (await response.json()) as { procedure_id: string };
  await expect(page).toHaveURL(new RegExp(`procedure=${receipt.procedure_id}`));
  await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: /^Open retained run:/ }).click();
  await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
  const run = new URL(page.url()).searchParams.get("run");
  expect(run).toBeTruthy();
  return run!;
}

const VERIFY = `
import sys
import scopecat as sc
from scopecat.records.run import ParameterRunConfigSource
with sc.open_project(sys.argv[1]).connect() as lab:
    run = lab.get_run(sys.argv[2])
    source = run.snapshot.config_source
    assert isinstance(source, ParameterRunConfigSource)
    expected = lab.parameters.get(sys.argv[3]).ref
    assert source.parameters == expected, (source.parameters, expected, run.id)
    assert source.setup == run.snapshot.execution_setup
    assert run.samples[0].sample_id == sys.argv[4]
    assert run.samples[0].revision == 1
    assert run.samples[0].batch_id == (sys.argv[6] or None)
    assert run.config.parameter_snapshot.get("signals").rows[0]["center"] == sc.Quantity(float(sys.argv[5]), "GHz")
    assert not lab.config.registry().entries
`;

for (const scoped of [false, true]) {
  test(`launches two samples with independent parameter versions${scoped ? " and declared batches" : ""}`, async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const project = await mkdtemp(join(tmpdir(), "scopecat-parameters-e2e-"));
    try {
      const url = await start(project);
      for (const sample of ["a", "b"]) {
        const created = await page.request.post(`${url}/api/v1/samples`, {
          data: {
            operation_id: `create-${sample}`,
            sample_id: `context-${sample}`,
            kind: "synthetic",
            actor: "operator",
            content: { display_name: `Sample ${sample}` },
          },
        });
        expect(created.status()).toBe(201);
        if (scoped) {
          const batch = await page.request.put(
            `${url}/api/v1/experimental-batches/cooldown-${sample}`,
            {
              data: { name: `cooldown-${sample}`, expected_revision: 0 },
            },
          );
          expect(batch.status()).toBe(200);
        }
      }
      await page.goto(`${url}/#configuration`);
      for (let index = 0; index < 4; index++) {
        const sample = index < 2 ? "a" : "b";
        const frequency = 4.8 + index / 10;
        const name = `values-${index}`;
        await editVersion(page, "browser-values");
        await page.getByLabel("signals[1].center", { exact: true }).fill(String(frequency));
        await page.getByLabel("signals[1].center unit", { exact: true }).fill("GHz");
        await saveVersion(page, name);
        await launchVersion(page);
        await page.getByLabel("Sample ID", { exact: true }).fill(`context-${sample}`);
        if (scoped) {
          const choices = page.getByRole("button", {
            name: /^(Browse samples, batches and collections|Hide context choices)$/,
          });
          if ((await choices.getAttribute("aria-expanded")) === "false") await choices.click();
          await expect(choices).toHaveAttribute("aria-expanded", "true");
          const batch = page.getByLabel("Experimental batch", { exact: true });
          await expect(batch).toBeVisible();
          await batch.selectOption(`cooldown-${sample}`);
          await expect(batch).toHaveValue(`cooldown-${sample}`);
        }
        if (index > 0) {
          await expect(
            page.getByRole("heading", { name: "Submission confirmed", exact: true }),
          ).toBeVisible();
          await expect(
            page.getByRole("button", { name: "Start acquisition", exact: true }),
          ).toBeDisabled();
          await page
            .getByRole("button", { name: "Prepare a new run (separate acquisition)", exact: true })
            .click();
          await expect(
            page.getByRole("button", { name: "Start acquisition", exact: true }),
          ).toBeDisabled();
        }
        const previewing = page.waitForResponse((response) =>
          response.url().endsWith("/experiment-launcher/preview"),
        );
        await page.getByRole("button", { name: "Preview", exact: true }).click();
        const response = await previewing;
        expect(response.status(), await response.text()).toBe(200);
        const prepared = await response.json();
        expect(prepared.reviewed.config_source).toMatchObject({
          kind: "parameter_revision",
          parameters: { revision_id: name },
        });
        expect(prepared.reviewed.binding.subject).toMatchObject({
          kind: "inline_samples",
          samples: [
            {
              sample_id: `context-${sample}`,
              revision: 1,
              ...(scoped ? { batch_id: `cooldown-${sample}` } : {}),
            },
          ],
        });
        const runId = await acquire(page);
        uv([
          "python",
          "-c",
          VERIFY,
          project,
          runId,
          name,
          `context-${sample}`,
          String(frequency),
          scoped ? `cooldown-${sample}` : "",
        ]);
      }
    } finally {
      uv(["scopecat", "stop", project]);
      await rm(project, { recursive: true, force: true });
    }
  });
}

test("opens an optional column created in Python and retains unknown values through GUI save and launch", async ({
  page,
}) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-structure-e2e-"));
  try {
    const url = await start(project);
    const optionalVersion = uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    params = lab.parameters.workspace("browser")
    params.add_column("signals", "quality", float | None)
    print(params.save().id)
`,
      project,
    ]);
    await page.goto(`${url}/#configuration`);
    await editVersion(page, optionalVersion);
    await expect(page.getByLabel("signals[1].quality", { exact: true })).toHaveValue("");
    await saveVersion(page, "gui-optional-quality");
    await launchVersion(page);
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    const run = await acquire(page);
    uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    run = lab.get_run(sys.argv[2])
    assert run.snapshot.config_source.parameters == lab.parameters.get("gui-optional-quality").ref
    table = run.config.parameter_catalog.get("signals").value_type
    assert any(column.id == "quality" for column in table.columns)
    assert all("quality" not in row for row in run.config.parameter_snapshot.get("signals").rows)
`,
      project,
      run,
    ]);
  } finally {
    uv(["scopecat", "stop", project]);
    await rm(project, { recursive: true, force: true });
  }
});

test("keeps keyboard edits and Python units across navigation without changing the original version", async ({
  page,
}, info) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-workspace-e2e-"));
  try {
    const url = await start(project);
    const pythonVersion = uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    params = lab.parameters.workspace("browser")
    params['signals']['a']['center'] = sc.Quantity(5100, 'MHz')
    print(params.save().id)
`,
      project,
    ]);
    await page.goto(`${url}/#configuration`);
    await editVersion(page, pythonVersion);
    const frequency = page.getByLabel("signals[1].center", { exact: true });
    await expect(frequency).toHaveValue("5100.0");
    await expect(page.getByLabel("signals[1].center unit", { exact: true })).toHaveValue("MHz");
    await frequency.focus();
    await frequency.press("ControlOrMeta+A");
    await frequency.pressSequentially("5200");
    await frequency.press("Tab");
    await page
      .getByRole("navigation", { name: "Project sections" })
      .getByRole("button", { name: "Runs", exact: true })
      .click();
    await page
      .getByRole("navigation", { name: "Project sections" })
      .getByRole("button", { name: "Configuration", exact: true })
      .click();
    await expect(frequency).toHaveValue("5200");
    await page.screenshot({
      path: info.outputPath("independent-parameter-draft.png"),
      fullPage: true,
    });
    await saveVersion(page, "gui-roundtrip");
    uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    old = lab.parameters.get(sys.argv[2])
    new = lab.parameters.get("gui-roundtrip")
    assert old.parameters.get("signals").rows[0]["center"] == sc.Quantity(5100, "MHz")
    assert new.parameters.get("signals").rows[0]["center"] == sc.Quantity(5200, "MHz")
    untouched = lambda revision: revision.parameters.get("signals").rows[1:]
    assert untouched(old) == untouched(new)
    assert old.catalog == new.catalog
    assert lab.parameters.checkout("browser").head.revision == old.ref
`,
      project,
      pythonVersion,
    ]);
  } finally {
    uv(["scopecat", "stop", project]);
    await rm(project, { recursive: true, force: true });
  }
});
