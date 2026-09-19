import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

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
async function createScope(page: Page, kind: "batch" | "collection", name: string) {
  const label = kind === "batch" ? "Experimental batch" : "Record collection";
  await page.getByRole("button", { name: `New ${kind}`, exact: true }).click();
  await page.getByLabel(`${label} name`, { exact: true }).fill(name);
  await page.getByRole("button", { name: `Create ${kind}`, exact: true }).click();
  await page.getByRole("button", { name: `Use ${name}`, exact: true }).click();
  return page.getByLabel(label, { exact: true }).inputValue();
}
async function preview(page: Page) {
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/experiment-launcher/preview") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  const result = await response;
  expect(result.status(), await result.text()).toBe(200);
  await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
  return result.json();
}
async function acquire(page: Page) {
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/experiment-launcher/submit") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
  const result = await response;
  expect(result.status(), await result.text()).toBe(200);
  await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
  return (await result.json()).procedure_id as string;
}

test("two workbench pages retain independent context and share collection numbering", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-workbench-e2e-"));
  let completed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const { base_url: url } = JSON.parse(
      await readFile(join(project, ".scopecat/daemon.json"), "utf8"),
    ) as { base_url: string };
    for (const id of ["chip-a", "chip-b"]) {
      const response = await page.request.post(`${url}/api/v1/samples`, {
        data: {
          operation_id: `create-${id}`,
          sample_id: id,
          kind: "synthetic",
          actor: "operator",
          content: { display_name: id },
        },
      });
      expect(response.status()).toBe(201);
    }
    await page.goto(`${url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await page.getByRole("button", { name: "Browse samples, batches and collections" }).click();
    await page.getByLabel("Registered sample", { exact: true }).selectOption("chip-a");
    await page.getByLabel("Operator", { exact: true }).fill("Alice");
    const batchA = await createScope(page, "batch", "Cooldown A");
    const collection = await createScope(page, "collection", "Shared measurements");
    const other = await context.newPage();
    await other.goto(`${url}/#launch`);
    await other.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await expect(other.getByLabel("Sample ID", { exact: true })).toHaveValue("");
    await expect(other.getByLabel("Operator", { exact: true })).toHaveValue("operator");
    await other.getByRole("button", { name: "Browse samples, batches and collections" }).click();
    await other.getByLabel("Registered sample", { exact: true }).selectOption("chip-b");
    await other.getByLabel("Operator", { exact: true }).fill("Bob");
    const batchB = await createScope(other, "batch", "Cooldown B");
    await other.getByLabel("Record collection", { exact: true }).selectOption(collection);
    await page
      .getByLabel("Experiment", { exact: true })
      .selectOption("reference_lab.frequency_amplitude");
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await expect(page.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-a");
    await expect(page.getByLabel("Operator", { exact: true })).toHaveValue("Alice");
    await expect(page.getByLabel("Experimental batch", { exact: true })).toHaveValue(batchA);
    await expect(page.getByLabel("Record collection", { exact: true })).toHaveValue(collection);
    const preparedA = await preview(page);
    expect(preparedA.reviewed.binding.subject).toMatchObject({
      kind: "inline_samples",
      samples: [{ sample_id: "chip-a", revision: 1, batch_id: batchA }],
    });
    await page.getByLabel("Plan name", { exact: true }).fill("First batch recipe");
    await page.getByRole("button", { name: "Save plan", exact: true }).click();
    await expect(page.getByText(/Saved First batch recipe, revision 1/)).toBeVisible();
    await page.getByLabel("Record collection", { exact: true }).selectOption("");
    const reopened = page.waitForRequest(
      (r) => r.url().endsWith("/experiment-launcher/preview") && r.method() === "POST",
    );
    await preview(page);
    expect((await reopened).postDataJSON().plan_ref).toMatchObject({ revision: 1 });
    await page.getByLabel("Record collection", { exact: true }).selectOption(collection);
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await preview(page);
    // Reload the other page's catalog without replacing its page-local selection.
    await other.getByRole("button", { name: "Refresh project data", exact: true }).click();
    await other.getByRole("button", { name: "Open First batch recipe r1", exact: true }).click();
    await expect(
      other.getByRole("alert").filter({ hasText: "This plan belongs to another batch" }),
    ).toBeVisible();
    await expect(other.getByLabel("Experimental batch", { exact: true })).toHaveValue(batchB);
    await expect(other.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-b");
    const preparedB = await preview(other);
    expect(preparedB.reviewed.binding.subject).toMatchObject({
      kind: "inline_samples",
      samples: [{ sample_id: "chip-b", revision: 1, batch_id: batchB }],
    });
    await page.screenshot({ path: testInfo.outputPath("page-context.png"), fullPage: true });
    const first = await acquire(page);
    const second = await acquire(other);
    uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
from scopecat.records.research_project import RunHistoryFilter
project = sc.open_project(sys.argv[1])
with project.connect() as lab:
    runs = lab._client.list_runs(history=RunHistoryFilter(record_collection=sys.argv[2]))
    assert len(runs.items) == 2
    snapshots = [lab.get_run(item.run_id).snapshot for item in runs.items]
    assert {s.samples[0].sample_id for s in snapshots} == {"chip-a", "chip-b"}
    assert {s.samples[0].batch_id for s in snapshots} == {sys.argv[3], sys.argv[4]}
    assert {lab._client.get_run(s.run_id).address.number for s in snapshots} == {1, 2}
`,
      project,
      collection,
      batchA,
      batchB,
      first,
      second,
    ]);
    completed = true;
    await other.close();
  } finally {
    uv(["scopecat", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved workbench project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
