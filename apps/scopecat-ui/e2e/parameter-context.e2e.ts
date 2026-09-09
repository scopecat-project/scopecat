import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]): void {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 30_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
}

const VERIFY = `
import sys
import scopecat as sc
from scopecat.records.config_context import ContextRunConfigSource
project = sc.open_project(sys.argv[1])
with project.connect() as lab:
    run = lab.get_run(sys.argv[2])
    assert isinstance(run.snapshot.config_source, ContextRunConfigSource)
    assert run.snapshot.config_source.context.entry_id == sys.argv[3]
    assert run.samples[0].sample_id == sys.argv[4]
    assert run.samples[0].revision == 1
    assert run.config.parameter_snapshot.get("qubits").rows[0]["drive_carrier_frequency"].value == float(sys.argv[5])
`;

test("saves and launches two physical samples at two working points without activating them", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-context-e2e-"));
  let completed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const active = await (
      await page.request.get(`${endpoint.base_url}/api/v1/config-registry?limit=100`)
    ).json();
    for (const sample of ["a", "b"]) {
      const created = await page.request.post(`${endpoint.base_url}/api/v1/samples`, {
        data: {
          operation_id: `create-${sample}`,
          sample_id: `context-${sample}`,
          kind: "synthetic",
          actor: "operator",
          content: { display_name: `Sample ${sample}` },
        },
      });
      expect(created.status()).toBe(201);
    }
    await page.goto(`${endpoint.base_url}/#configuration`);
    for (let index = 0; index < 4; index++) {
      const sample = index < 2 ? "a" : "b";
      const point = index % 2 === 0 ? "parked" : "shifted";
      const frequency = 4.8e9 + index * 1e8;
      await page.getByRole("button", { name: "Configuration", exact: true }).click();
      await page.getByRole("button", { name: "Save working point copy", exact: true }).click();
      await page.getByLabel("Physical sample", { exact: true }).selectOption(`context-${sample}@1`);
      await page.getByLabel("Working point", { exact: true }).fill(point);
      await page.getByLabel("Context label", { exact: true }).fill(`${sample} ${point}`);
      await page
        .getByLabel("qubits[0].drive_carrier_frequency", { exact: true })
        .fill(String(frequency));
      const saving = page.waitForResponse(
        (response) =>
          response.url().endsWith("/config-registry/contexts") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Save context", exact: true }).click();
      const savedResponse = await saving;
      expect(savedResponse.status()).toBe(200);
      const saved = await savedResponse.json();
      const catalogReady = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname.endsWith("/experiment-launcher") &&
          response.request().method() === "GET",
      );
      await page.getByRole("button", { name: "Use for next experiment", exact: true }).click();
      const catalogResponse = await catalogReady;
      expect(catalogResponse.status(), await catalogResponse.text()).toBe(200);
      await expect(page.getByLabel("Experiment", { exact: true })).toBeVisible();
      await expect(
        page.getByText(new RegExp(`Parameter context: ${saved.entry.id}`)),
      ).toBeVisible();
      await page
        .getByLabel("Experiment", { exact: true })
        .selectOption(index % 2 === 0 ? "signal" : "frequency-amplitude");
      await page.getByLabel("Frequency", { exact: true }).fill(String(4.8 + index / 10));
      const previewing = page.waitForResponse((response) =>
        response.url().endsWith("/experiment-launcher/preview"),
      );
      await page.getByRole("button", { name: "Preview", exact: true }).click();
      const preview = await previewing;
      expect(preview.status()).toBe(200);
      expect((await preview.json()).config_source).toMatchObject({
        kind: "parameter_context",
        context: { entry_id: saved.entry.id },
        sample: { sample_id: `context-${sample}`, revision: 1, context_id: point },
      });
      await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
      await expect(
        page.getByText(index % 2 === 0 ? "experiment: Completed" : "signal: Completed", {
          exact: true,
        }),
      ).toBeVisible();
      await page.getByRole("link", { name: /^Open retained run:/ }).click();
      await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
      const runId = new URL(page.url()).searchParams.get("run");
      expect(runId).toBeTruthy();
      uv([
        "python",
        "-c",
        VERIFY,
        project,
        runId!,
        saved.entry.id,
        `context-${sample}`,
        String(frequency),
      ]);
    }
    const after = await (
      await page.request.get(`${endpoint.base_url}/api/v1/config-registry?limit=100`)
    ).json();
    expect(after.activation).toEqual(active.activation);
    expect(
      after.entries.filter(
        (entry: { source: { kind: string } }) => entry.source.kind === "parameter_context",
      ),
    ).toHaveLength(4);
    completed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved context project", {
        body: project,
        contentType: "text/plain",
      });
  }
});

test("adds an unknown optional table column through the GUI and launches the saved structure", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-structure-e2e-"));
  let completed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const active = await (
      await page.request.get(`${endpoint.base_url}/api/v1/config-registry?limit=100`)
    ).json();
    const created = await page.request.post(`${endpoint.base_url}/api/v1/samples`, {
      data: {
        operation_id: "create-structure",
        sample_id: "structure-a",
        kind: "synthetic",
        actor: "operator",
        content: { display_name: "Structure A" },
      },
    });
    expect(created.status()).toBe(201);
    await page.goto(`${endpoint.base_url}/#configuration`);
    await page.getByRole("button", { name: "Save working point copy", exact: true }).click();
    await page.getByLabel("Physical sample", { exact: true }).selectOption("structure-a@1");
    await page.getByLabel("Working point", { exact: true }).fill("parked");
    await page.getByLabel("Context label", { exact: true }).fill("Original structure");
    await page.getByRole("button", { name: "Save context", exact: true }).click();
    await page.getByRole("button", { name: "Change table structure", exact: true }).click();
    await page.getByLabel("Structure table", { exact: true }).selectOption("qubits");
    await page.getByLabel("Structure column ID", { exact: true }).fill("quality");
    await page
      .getByLabel("Structure note", { exact: true })
      .fill("Optional analysis column, values are unknown");
    await page.getByRole("button", { name: "Preview structure change", exact: true }).click();
    await expect(page.getByLabel("Structure impact preview")).toContainText("qubits[0].quality");
    const saving = page.waitForResponse(
      (response) =>
        response.url().endsWith("/config-registry/contexts") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Save revised working point", exact: true }).click();
    const savedResponse = await saving;
    expect(savedResponse.status()).toBe(200);
    const saved = await savedResponse.json();
    await expect(
      page
        .getByText("unknown · Optional analysis column, values are unknown", { exact: true })
        .first(),
    ).toBeVisible();
    expect(saved.entry.source.context.structure.edits[0]).toMatchObject({
      kind: "add_column",
      parameter_id: "qubits",
      column: { id: "quality" },
    });
    await page.getByRole("button", { name: "Use for next experiment", exact: true }).click();
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: /^Open retained run:/ }).click();
    await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
    const runId = new URL(page.url()).searchParams.get("run");
    expect(runId).toBeTruthy();
    uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    run = lab.get_run(sys.argv[2])
    assert run.snapshot.config_source.context.entry_id == sys.argv[3]
    table = run.config.parameter_catalog.get("qubits").value_type
    assert any(column.id == "quality" for column in table.columns)
    assert all("quality" not in row for row in run.config.parameter_snapshot.get("qubits").rows)
`,
      project,
      runId!,
      saved.entry.id,
    ]);
    const after = await (
      await page.request.get(`${endpoint.base_url}/api/v1/config-registry?limit=100`)
    ).json();
    expect(after.activation).toEqual(active.activation);
    completed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved structure project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
