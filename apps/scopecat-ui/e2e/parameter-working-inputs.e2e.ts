import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer } from "node:net";
import { expect, test, type Page } from "@playwright/test";
import { prepareReferenceContexts, reviewRetainedExperiment } from "./reference-context";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    env,
    encoding: "utf8",
    timeout: 30000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
  return result.stdout.trim();
}
async function openTable(page: Page, url: string) {
  await page.goto(`${url}/#configuration`);
  await page.getByLabel("Working parameter branch", { exact: true }).fill("browser");
  await page.getByRole("button", { name: "Open working table", exact: true }).click();
  await expect(page.getByLabel("qubits[1].drive_carrier_frequency", { exact: true })).toBeVisible();
}
async function preview(page: Page) {
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith("/experiment-launcher/preview") && item.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  const result = await response;
  expect(result.status(), await result.text()).toBe(200);
  return result.json();
}
async function submit(page: Page) {
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith("/experiment-launcher/submit") && item.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
  const result = await response;
  expect(result.status(), await result.text()).toBe(200);
  return (await result.json()).procedure_id as string;
}
async function retainedRun(page: Page) {
  await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
  const link = page.getByRole("link", { name: /^Open retained run:/ });
  await expect(link).toBeVisible();
  const href = await link.getAttribute("href");
  return new URL(href!, page.url()).searchParams.get("run")!;
}

test("working B survives restart, invalidates stale previews and submits frozen inputs", async ({
  browser,
}, testInfo) => {
  test.setTimeout(180000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-working-inputs-"));
  const blocker = createServer();
  const endpoint = async () =>
    JSON.parse(await readFile(join(home, ".scopecat/daemon.json"), "utf8")).base_url as string;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(home, name), { recursive: true });
    uv(["scopecat", "start", home, "--port", "0", "--static-dir", resolve("dist")]);
    prepareReferenceContexts(uv, home);
    const firstUrl = await endpoint();
    const context = await browser.newContext();
    const b = await context.newPage();
    const c = await context.newPage();
    await openTable(b, firstUrl);
    await openTable(c, firstUrl);
    const field = b.getByLabel("qubits[1].drive_carrier_frequency", { exact: true });
    await field.fill("1e");
    await b.getByLabel("Source or reason for changes").fill("B unfinished raw input");
    await expect(b.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    await c
      .getByLabel("Source or reason for changes")
      .fill("separate window's retained conflicting input");
    await expect(c.getByText(/Your conflicting copy is retained/)).toBeVisible();
    await b
      .getByRole("button", { name: "Use working inputs for next experiment", exact: true })
      .click();
    await expect(b.getByRole("alert")).toContainText("Complete working input");
    await b.goto(`${firstUrl}/#runs`);
    await openTable(b, firstUrl);
    await expect(field).toHaveValue("1e");
    await context.close();
    uv(["scopecat", "stop", home]);
    await new Promise<void>((done, reject) => {
      blocker.once("error", reject);
      blocker.listen(Number(new URL(firstUrl).port), "127.0.0.1", done);
    });
    uv(["scopecat", "start", home, "--port", "0", "--static-dir", resolve("dist")]);
    const url = await endpoint();
    expect(url).not.toBe(firstUrl);
    const fresh = await browser.newContext();
    const work = await fresh.newPage();
    await openTable(work, url);
    await expect(work.getByLabel("qubits[1].drive_carrier_frequency", { exact: true })).toHaveValue(
      "1e",
    );
    await expect(work.getByLabel("Source or reason for changes")).toHaveValue(
      "B unfinished raw input",
    );
    await work.getByLabel("qubits[1].drive_carrier_frequency", { exact: true }).fill("5.2");
    await work.getByLabel("qubits[1].drive_carrier_frequency unit", { exact: true }).fill("GHz");
    await work
      .getByRole("button", { name: "Use working inputs for next experiment", exact: true })
      .click();
    await work.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await work.getByLabel("Experiment setup", { exact: true }).selectOption("browser-bench-a");
    await expect(work.getByText(/Using working input revision/)).toBeVisible();
    const oldB = await preview(work);
    expect(oldB.reviewed.config_source.overrides).toHaveLength(1);
    const editor = await fresh.newPage();
    await openTable(editor, url);
    await editor.getByLabel("qubits[1].drive_carrier_frequency", { exact: true }).fill("5.3");
    await expect(
      editor.getByText("Draft saved in application data", { exact: true }),
    ).toBeVisible();
    await expect(
      work.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await expect(work.getByText(/Working inputs changed or could not be checked/)).toBeVisible();
    await work.getByLabel("Gain", { exact: true }).fill("2");
    await work.getByRole("button", { name: "Use current working inputs", exact: true }).click();
    await expect(
      work.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    const previewB = await preview(work);
    expect(previewB.request_hash).not.toBe(oldB.request_hash);
    expect(previewB.reviewed.config_source.parameters).toEqual(
      oldB.reviewed.config_source.parameters,
    );
    const procedureB = await submit(work);
    const runB = await retainedRun(work);
    // Later working edits must not replace the submitted capture.
    await editor.getByLabel("qubits[1].drive_carrier_frequency", { exact: true }).fill("5.4");
    await expect(
      editor.getByText("Draft saved in application data", { exact: true }),
    ).toBeVisible();
    const evidence = uv([
      "python",
      "-c",
      `
import json,sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    b=lab.get_run(sys.argv[2])
    assert len(b.snapshot.config_source.overrides) == 1
    bv=b.config.parameter_snapshot.get('qubits').rows[0]['drive_carrier_frequency']
    assert bv.to('GHz') == sc.Quantity(5.3,'GHz'), bv
    assert lab.parameters.checkout('browser').head.generation == 1
    assert len(lab.parameters.list()) == 1
    print(json.dumps({'b':b.id,'parameters':b.snapshot.config_source.parameters.model_dump(mode='json'),'b_frequency':str(bv),'branch_generation':1}))
`,
      home,
      runB,
    ]);
    expect(JSON.parse(evidence).parameters).toEqual(previewB.reviewed.config_source.parameters);
    // Keep the original result selected while another window submits a newer run.
    await work.getByRole("link", { name: /^Open retained run:/ }).click();
    await expect(work.getByTitle(runB, { exact: true })).toBeVisible();
    await editor
      .getByRole("button", { name: "Use working inputs for next experiment", exact: true })
      .click();
    await editor.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await editor.getByLabel("Experiment setup", { exact: true }).selectOption("browser-bench-a");
    await reviewRetainedExperiment(editor);
    await expect(editor.getByText(/Working inputs changed or could not be checked/)).toBeVisible();
    await expect(editor.getByRole("button", { name: "Preview", exact: true })).toBeDisabled();
    await editor.getByRole("button", { name: "Use current working inputs", exact: true }).click();
    await expect(
      editor.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await preview(editor);
    const procedureC = await submit(editor);
    const runC = await retainedRun(editor);
    expect(runC).not.toBe(runB);
    uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    for run_id, frequency in ((sys.argv[2], 5.3), (sys.argv[3], 5.4)):
        run = lab.get_run(run_id)
        value = run.config.parameter_snapshot.get('qubits').rows[0]['drive_carrier_frequency']
        assert value.to('GHz') == sc.Quantity(frequency, 'GHz'), (run_id, value)
`,
      home,
      runB,
      runC,
    ]);
    await editor.getByRole("link", { name: /^Open retained run:/ }).click();
    await expect(editor.getByTitle(runC, { exact: true })).toBeVisible();
    await work.getByRole("button", { name: "Configuration", exact: true }).click();
    const continuedField = work.getByLabel("qubits[1].drive_carrier_frequency", { exact: true });
    // Explicitly reopen the latest shared draft before making a new edit.
    await work.getByRole("button", { name: "Close editor", exact: true }).click();
    await work.getByRole("button", { name: "Open working table", exact: true }).click();
    await expect(continuedField).toHaveValue("5.4");
    await continuedField.fill("5.5");
    await expect(work.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    await work.getByRole("button", { name: "Runs", exact: true }).click();
    await expect(work.getByTitle(runB, { exact: true })).toBeVisible();
    expect(new URL(work.url()).searchParams.get("run")).toBe(runB);
    await expect(editor.getByTitle(runC, { exact: true })).toBeVisible();
    await work.getByRole("button", { name: "Experiments", exact: true }).click();
    expect(new URL(work.url()).searchParams.get("procedure")).toBe(procedureB);
    await expect(work.getByRole("link", { name: /^Open retained run:/ })).toHaveAttribute(
      "href",
      `?procedure=${encodeURIComponent(procedureB)}&run=${encodeURIComponent(runB)}#runs`,
    );
    // Reopening the same procedure must not reload away this window's launch draft.
    await expect(work.getByLabel("Gain", { exact: true })).toHaveValue("2");
    await work.getByRole("link", { name: "Reopen this procedure", exact: true }).click();
    await expect(work.getByLabel("Gain", { exact: true })).toHaveValue("2");
    await expect(work.getByText(/Using working input revision/)).toBeVisible();
    await work.getByRole("button", { name: "Configuration", exact: true }).click();
    await expect(continuedField).toHaveValue("5.5");
    await testInfo.attach("working-inputs-evidence", {
      body: JSON.stringify({
        firstUrl,
        url,
        procedureB,
        procedureC,
        runB,
        runC,
        evidence,
        previewB: previewB.request_hash,
      }),
      contentType: "application/json",
    });
    await fresh.close();
  } finally {
    if (blocker.listening) await new Promise<void>((done) => blocker.close(() => done()));
    uv(["scopecat", "stop", home]);
    await rm(home, { recursive: true, force: true });
  }
});

test("failed draft save cancels browser exit and recovers after retry", async ({ browser }) => {
  test.setTimeout(120000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-working-close-"));
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(home, name), { recursive: true });
    uv(["scopecat", "start", home, "--port", "0", "--static-dir", resolve("dist")]);
    prepareReferenceContexts(uv, home);
    const url = JSON.parse(await readFile(join(home, ".scopecat/daemon.json"), "utf8"))
      .base_url as string;
    const context = await browser.newContext();
    const page = await context.newPage();
    await openTable(page, url);
    await page.route("**/parameter-drafts/*/save", (route) => route.abort("connectionfailed"));
    const field = page.getByLabel("qubits[1].drive_carrier_frequency", { exact: true });
    await field.fill("1e");
    await expect(page.getByRole("button", { name: "Retry draft save", exact: true })).toBeVisible();
    const prompt = page.waitForEvent("dialog").then(async (dialog) => {
      expect(dialog.type()).toBe("beforeunload");
      await dialog.dismiss();
    });
    await page.close({ runBeforeUnload: true });
    await prompt;
    expect(page.isClosed()).toBe(false);
    await expect(field).toHaveValue("1e");
    await page.unroute("**/parameter-drafts/*/save");
    await page.getByRole("button", { name: "Retry draft save", exact: true }).click();
    await expect(page.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close editor", exact: true }).click();
    await expect(field).not.toBeVisible();
    await page.getByRole("button", { name: "Open working table", exact: true }).click();
    await expect(field).toHaveValue("1e");
    await expect(page.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    await context.close();
  } finally {
    uv(["scopecat", "stop", home]);
    await rm(home, { recursive: true, force: true });
  }
});
