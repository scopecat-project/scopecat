import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  chooseAuthorContext,
  copyAuthorWorkspace,
  prepareAuthorContexts,
  reviewRetainedExperiment,
} from "./author-context";

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
  await page.getByRole("button", { name: "Open submitted procedure", exact: true }).click();
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
    await copyAuthorWorkspace(project);
    uv([
      "python",
      "-m",
      "scopecat_server.cli",
      "start",
      project,
      "--port",
      "0",
      "--static-dir",
      resolve("dist"),
    ]);
    prepareAuthorContexts(uv, project);
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
    await page.goto(`${url}/#settings`);
    await page.getByLabel("Operator name", { exact: true }).fill("operator");
    await page.getByRole("button", { name: "Save operator", exact: true }).click();
    await expect(page.getByText("Operator saved", { exact: true })).toBeVisible();
    await page.goto(`${url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    await expect(page.getByLabel("Sample ID", { exact: true })).toHaveValue("");
    await expect(page.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue(
      "operator",
    );
    await page.getByRole("button", { name: "Browse samples, batches and collections" }).click();
    await page.getByLabel("Registered sample", { exact: true }).selectOption("chip-a");
    await page.getByRole("textbox", { name: "Operator", exact: true }).fill("Alice");
    const batchA = await createScope(page, "batch", "Cooldown A");
    const collection = await createScope(page, "collection", "Shared measurements");
    // Finish A's explicit experiment round trip before another page edits the shared draft.
    await page.getByLabel("Experiment", { exact: true }).selectOption("constant");
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await expect(page.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-a");
    await expect(page.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue("Alice");
    await expect(page.getByLabel("Experimental batch", { exact: true })).toHaveValue(batchA);
    await expect(page.getByLabel("Record collection", { exact: true })).toHaveValue(collection);
    await reviewRetainedExperiment(page);
    await expect(
      page.getByText("Experiment input saved in application data.", { exact: true }),
    ).toBeVisible();
    const other = await context.newPage();
    await other.goto(`${url}/#launch`);
    await other.getByLabel("Experiment", { exact: true }).selectOption("signal");
    // New windows recover the saved experiment; no acquisition is authorized by recovery.
    await expect(other.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-a");
    await expect(other.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue(
      "Alice",
    );
    await expect(
      other.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await reviewRetainedExperiment(other);
    await chooseAuthorContext(other, "browser-bench-b");
    await other.getByRole("button", { name: "Browse samples, batches and collections" }).click();
    await other.getByLabel("Registered sample", { exact: true }).selectOption("chip-b");
    await other.getByRole("textbox", { name: "Operator", exact: true }).fill("Bob");
    const batchB = await createScope(other, "batch", "Cooldown B");
    await other.getByLabel("Record collection", { exact: true }).selectOption(collection);
    // A lesson uses the same application while both ordinary drafts stay open.
    const practice = await context.newPage();
    // Help resumes retained peak practices; new learning starts in Notebook courses.
    // Seed the retained practice through its real service entry before exercising recovery.
    const retainedPractice = await practice.request.post(`${url}/api/v1/practice`, {
      data: { lesson: "manual-peaks", request_key: "workbench-retained-practice" },
    });
    expect(retainedPractice.ok(), await retainedPractice.text()).toBeTruthy();
    await practice.goto(`${url}/#help`);
    const retainedTask = practice.getByRole("link", { name: "Open practice task" });
    await expect(retainedTask).toBeVisible();
    const taskLink = await retainedTask.getAttribute("href");
    await practice.reload();
    await expect(retainedTask).toHaveAttribute("href", taskLink!);
    await practice.getByRole("button", { name: "Clear practice…" }).click();
    await expect(practice.getByRole("combobox", { name: "Practice files" })).toHaveValue(
      "preserve",
    );
    await practice.getByRole("button", { name: "Clear this practice" }).click();
    await expect(practice.getByText("Practice cleared", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue("Alice");
    await expect(other.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue("Bob");
    await expect(other.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-b");
    await practice.close();
    const preparedA = await preview(page);
    const resolvedA = await (
      await page.request.get(
        `${url}/api/v1/setup/revisions/${encodeURIComponent(preparedA.reviewed.config_source.setup.revision_id)}`,
      )
    ).json();
    expect(resolvedA.resolution.definition_id).toBe("browser-bench-a");
    expect(preparedA.reviewed.binding.subject).toMatchObject({
      kind: "inline_samples",
      samples: [{ sample_id: "chip-a", revision: 1, batch_id: batchA }],
    });
    await page.getByLabel("Plan name", { exact: true }).fill("First batch recipe");
    await page.getByRole("button", { name: "Save plan", exact: true }).click();
    await expect(page.getByText(/Saved First batch recipe, revision 1/)).toBeVisible();
    const conflictA = page.getByRole("region", { name: "Conflicting experiment input" });
    await expect(conflictA).toContainText("Bob");
    await expect(page.getByRole("button", { name: "Preview", exact: true })).toBeDisabled();
    await conflictA.getByRole("button", { name: "Keep my copy after review" }).click();
    await expect(conflictA).toBeHidden();
    await page.getByLabel("Record collection", { exact: true }).selectOption("");
    const reopened = page.waitForRequest(
      (r) => r.url().endsWith("/experiment-launcher/preview") && r.method() === "POST",
    );
    await preview(page);
    expect((await reopened).postDataJSON().plan_ref).toMatchObject({ revision: 1 });
    await page.getByLabel("Record collection", { exact: true }).selectOption(collection);
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeEnabled();
    await expect(page.getByText("Preview ready", { exact: true })).toHaveCount(0);
    await preview(page);
    // Reload the other page's catalog without replacing its page-local selection.
    await other.getByRole("button", { name: "Refresh project data", exact: true }).click();
    await expect(other.getByLabel("Experimental batch", { exact: true })).toHaveValue(batchB);
    await expect(other.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-b");
    await expect(other.getByLabel("Experiment setup", { exact: true })).toHaveValue(
      "browser-bench-b",
    );
    await other.getByRole("button", { name: "Open First batch recipe r1", exact: true }).click();
    // Explicitly opening a plan imports its scientific selection into this page.
    await expect(other.getByLabel("Experimental batch", { exact: true })).toHaveValue(batchA);
    await expect(other.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-a");
    await expect(other.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue("Bob");
    await expect(other.getByLabel("Record collection", { exact: true })).toHaveValue(collection);
    await expect(
      other.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeEnabled();
    await expect(other.getByText("Preview ready", { exact: true })).toHaveCount(0);
    await other.getByLabel("Registered sample", { exact: true }).selectOption("chip-b");
    await other.getByLabel("Experimental batch", { exact: true }).selectOption(batchB);
    await other.getByLabel("Experiment setup", { exact: true }).selectOption("browser-bench-b");
    await expect(page.getByLabel("Experimental batch", { exact: true })).toHaveValue(batchA);
    await expect(page.getByLabel("Sample ID", { exact: true })).toHaveValue("chip-a");
    await expect(page.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue("Alice");
    const conflictB = other.getByRole("region", { name: "Conflicting experiment input" });
    await expect(conflictB).toContainText("Alice");
    await expect(other.getByRole("button", { name: "Preview", exact: true })).toBeDisabled();
    await conflictB.getByRole("button", { name: "Keep my copy after review" }).click();
    await expect(conflictB).toBeHidden();
    const preparedB = await preview(other);
    const resolvedB = await (
      await other.request.get(
        `${url}/api/v1/setup/revisions/${encodeURIComponent(preparedB.reviewed.config_source.setup.revision_id)}`,
      )
    ).json();
    expect(resolvedB.resolution.definition_id).toBe("browser-bench-b");
    expect(resolvedB.resolution.devices).toEqual(resolvedA.resolution.devices);
    expect(preparedB.reviewed.binding.setup_content_hash).not.toBe(
      preparedA.reviewed.binding.setup_content_hash,
    );
    expect(preparedB.reviewed.binding.subject).toMatchObject({
      kind: "inline_samples",
      samples: [{ sample_id: "chip-b", revision: 1, batch_id: batchB }],
    });
    await page.screenshot({ path: testInfo.outputPath("page-context.png"), fullPage: true });
    const first = await acquire(page);
    const second = await acquire(other);
    // A subsequent acquisition keeps its page's collection until explicitly changed.
    await other.getByRole("link", { name: "Return to preparation", exact: true }).first().click();
    await expect(other.getByLabel("Record collection", { exact: true })).toHaveValue(collection);
    await other.getByRole("button", { name: "Prepare another acquisition", exact: true }).click();
    await expect(other.getByLabel("Record collection", { exact: true })).toHaveValue(collection);
    const separate = await createScope(other, "collection", "Separate measurements");
    await preview(other);
    await acquire(other);
    const readAddress = async (id: string, number: number) => {
      const response = await page.request.get(
        `${url}/api/v1/record-collections/${id}/runs/${number}`,
      );
      expect(response.ok()).toBe(true);
      return response.json() as Promise<{ run_id: string; collection_id: string; number: number }>;
    };
    const firstAddress = await readAddress(collection, 1);
    const secondAddress = await readAddress(collection, 2);
    const separateAddress = await readAddress(separate, 1);
    expect(new Set([firstAddress.run_id, secondAddress.run_id, separateAddress.run_id]).size).toBe(
      3,
    );
    await other.getByRole("link", { name: "Return to preparation", exact: true }).first().click();
    await page.getByRole("button", { name: "Data", exact: true }).click();
    await page.getByLabel("Record collection filter", { exact: true }).selectOption(collection);
    await expect(
      page.getByRole("region", { name: "Data", exact: true }).getByTestId("run-address"),
    ).toHaveCount(2);
    await page.getByText("Manage collection", { exact: true }).click();
    await page.getByRole("button", { name: "Rename collection", exact: true }).click();
    await page.getByLabel("New collection name", { exact: true }).fill("Renamed measurements");
    await page.getByRole("button", { name: "Save collection name", exact: true }).click();
    await expect(
      page.getByRole("option", { name: "Renamed measurements", exact: true }),
    ).toBeAttached();
    await page.getByLabel("Collection run number", { exact: true }).fill("1");
    await page.getByRole("button", { name: "Find run", exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath("collection-browser.png"), fullPage: true });
    await page.getByRole("button", { name: "Open collection run #1", exact: true }).click();
    const header = page.getByTestId("run-detail-header");
    await expect(header).toContainText(firstAddress.run_id);
    await expect(header).toContainText("Renamed measurements");
    await expect(header.getByTestId("run-address")).toHaveText("Renamed measurements · #1");
    await expect(header.getByTestId("run-address")).toHaveAttribute(
      "title",
      new RegExp(collection),
    );
    await expect(header.getByTestId("run-address")).toHaveAttribute("title", /Global #/);
    // Copy the UI's actual snippet, without using the host clipboard.
    await page.evaluate(() =>
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined }),
    );
    await header.getByRole("button", { name: "Copy read-only code" }).click();
    const copiedCode = await header.getByLabel("Read-only code").inputValue();
    expect(copiedCode).toContain(`session.run(1, collection="${collection}")`);
    const opened = context.waitForEvent("page");
    await page.getByRole("link", { name: "Open result in new tab or window" }).click();
    const resultWindow = await opened;
    await expect(resultWindow.getByTestId("run-detail-header")).toContainText(firstAddress.run_id);
    await expect(resultWindow).toHaveTitle(/Renamed measurements #1/);
    await resultWindow.close();
    await page.goBack();
    await expect(page.getByLabel("Record collection filter", { exact: true })).toHaveValue(
      collection,
    );
    await expect(page.getByLabel("Collection run number")).toHaveValue("1");
    await page.getByLabel("Record collection filter", { exact: true }).selectOption(separate);
    await page.getByLabel("Collection run number").fill("1");
    await page.getByRole("button", { name: "Find run", exact: true }).click();
    await page.getByRole("button", { name: "Open collection run #1", exact: true }).click();
    await expect(header).toContainText(separateAddress.run_id);
    await expect(other.getByLabel("Record collection", { exact: true })).toHaveValue(separate);
    await expect(page.getByTestId("measurement-charts")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("collection-result.png"), fullPage: true });
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    uv([
      "python",
      "-m",
      "scopecat_server.cli",
      "start",
      project,
      "--port",
      "0",
      "--static-dir",
      resolve("dist"),
    ]);
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
    assert {lab.setup.revision(s.config_source.setup.revision_id).resolution.definition_id for s in snapshots} == {"browser-bench-a", "browser-bench-b"}
    assert {lab._client.get_run(s.run_id).address.number for s in snapshots} == {1, 2}
from IPython.core.interactiveshell import InteractiveShell
InteractiveShell.instance()
with sc.notebook(sys.argv[1], live=False) as session:
    session.use(collection=sys.argv[7])
    assert session.run(1).id == sys.argv[8]
    assert session.run(1, collection=sys.argv[2]).id == sys.argv[9]
    exec(sys.argv[10])
    assert run.id == sys.argv[9]
    assert session.run_number(run, collection=sys.argv[2]) == 1
    assert session.run(session.run_number(run, collection=None), collection=None).id == run.id
`,
      project,
      collection,
      batchA,
      batchB,
      first,
      second,
      separate,
      separateAddress.run_id,
      firstAddress.run_id,
      copiedCode,
    ]);
    completed = true;
    await other.close();
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved workbench project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
