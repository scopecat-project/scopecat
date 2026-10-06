import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { chooseReferenceContext, prepareReferenceContexts } from "./reference-context";

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

test("discovers an ordinary author experiment and edits controls before submitting", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-author-e2e-"));
  let completed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    prepareReferenceContexts(uv, project);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await chooseReferenceContext(page);
    await expect(page.getByLabel("Frequency", { exact: true })).toHaveValue("4.8");
    await page.getByLabel("Gain", { exact: true }).fill("2");
    await page.getByLabel("Polarity", { exact: true }).selectOption("negative");
    await page.getByLabel("Frequency source").selectOption("range");
    await page.getByLabel("Frequency start").fill("4.7");
    await page.getByLabel("Frequency stop").fill("4.9");
    await page.getByLabel("Frequency points").fill("3");
    const previewResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/preview") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    const preview = await previewResponse;
    expect(preview.status()).toBe(200);
    expect(preview.request().postDataJSON().inputs).toEqual({ polarity: "negative" });
    const originalPreview = await preview.json();
    expect(originalPreview).toMatchObject({
      experiment_id: "signal",
      point_count: 3,
      controls: expect.arrayContaining([
        expect.objectContaining({ id: "frequency", state: "scanned" }),
      ]),
    });
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    const sourcePath = join(project, "src/reference_lab_authors/authored/signal.py");
    const originalSource = await readFile(sourcePath, "utf8");
    await writeFile(sourcePath, originalSource + "\ndef broken(:\n");
    await page.getByRole("button", { name: "Refresh author code", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "SyntaxError" })).toBeVisible();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    await writeFile(sourcePath, originalSource.replace("return gain /", "return 2 * gain /"));
    await page.getByRole("button", { name: "Refresh author code", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Author code refreshed" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByText(
        "Experiment revision changed. Inputs and control edits are retained; preview again.",
      ),
    ).toBeVisible();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("2");
    await expect(page.getByLabel("Frequency points")).toHaveValue("3");
    const nextPreviewResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/preview") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    const revisedPreview = await (await nextPreviewResponse).json();
    expect(revisedPreview.code_revision).not.toEqual(originalPreview.code_revision);
    expect(revisedPreview.point_count).toBe(3);
    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/submit") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const admitted = await submitted;
    expect(admitted.status()).toBe(200);
    expect(admitted.request().postDataJSON().inputs).toEqual({ polarity: "negative" });
    expect(await admitted.json()).toMatchObject({ dispatch_error: null });
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: /^Open retained run:/ }).click();
    await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
    await expect(page.getByText("Measurement data", { exact: true })).toBeVisible();
    completed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved author project", {
        body: project,
        contentType: "text/plain",
      });
  }
});

test("prepares B while A stays pinned in a separate result page", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-author-ab-"));
  const release = join(project, "release-a");
  const submissions: string[] = [];
  context.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/experiment-launcher/submit"))
      submissions.push(request.postData() ?? "");
  });
  let completed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    const sourcePath = join(project, "src/reference_lab_authors/authored/signal.py");
    const source = (await readFile(sourcePath, "utf8")).replace(
      "    detuning =",
      `    import time\n    from pathlib import Path\n    if gain == 2 and frequency.to("GHz").value > 4.7:\n        deadline = time.monotonic() + 90\n        while not Path(${JSON.stringify(release)}).exists():\n            if time.monotonic() > deadline:\n                raise RuntimeError("A was not released")\n            time.sleep(0.05)\n    detuning =`,
    );
    await writeFile(sourcePath, source);
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    prepareReferenceContexts(uv, project);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8"));
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await chooseReferenceContext(page);
    await page.getByLabel("Gain", { exact: true }).fill("2");
    await page.getByLabel("Frequency source").selectOption("range");
    await page.getByLabel("Frequency start").fill("4.7");
    await page.getByLabel("Frequency stop").fill("4.9");
    await page.getByLabel("Frequency points").fill("3");
    const preview = async () => {
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/experiment-launcher/preview") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Preview", exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(200);
      return result.json();
    };
    const submit = async () => {
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/experiment-launcher/submit") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(200);
      return result.json();
    };
    const previewA = await preview();
    const receiptA = await submit();
    const procedureA = new URL(page.url()).searchParams.get("procedure");
    const opened = context.waitForEvent("page");
    await page.getByRole("link", { name: "Open result in new tab or window" }).first().click();
    const resultA = await opened;
    await expect(resultA.getByTestId("run-status")).toHaveText("Running");
    const runA = new URL(resultA.url()).searchParams.get("run");
    const readA = async () => {
      const response = await context.request.get(`${endpoint.base_url}/api/v1/runs/${runA}`);
      expect(response.ok()).toBe(true);
      return response.json();
    };
    const acceptedA = await readA();
    await page.setViewportSize({ width: 520, height: 800 });
    await page.getByLabel("Gain", { exact: true }).fill("3");
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await writeFile(sourcePath, source.replace("return gain /", "return 2 * gain /"));
    await page.getByRole("button", { name: "Refresh author code", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Author code refreshed" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Help", exact: true }).click();
    await page.goBack();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await page.goForward();
    await expect(page.getByRole("button", { name: "Experiments", exact: true })).toBeVisible();
    await page.goBack();
    const previewB = await preview();
    expect(previewB.code_revision).not.toEqual(previewA.code_revision);
    await expect(resultA.getByTestId("run-status")).toHaveText("Running");
    const receiptB = await submit();
    expect(receiptB).not.toEqual(receiptA);
    const procedureB = new URL(page.url()).searchParams.get("procedure");
    expect(procedureB).not.toBe(procedureA);
    await writeFile(release, "");
    await expect(resultA.getByTestId("run-status")).toHaveText("Succeeded");
    await expect(resultA.getByTestId("data-card").getByText(/^3 records/)).toBeVisible();
    expect(new URL(resultA.url()).searchParams.get("run")).toBe(runA);
    const finishedA = await readA();
    expect(finishedA.control.admission).toEqual(acceptedA.control.admission);
    expect(finishedA.snapshot.config_source).toEqual(acceptedA.snapshot.config_source);
    expect(finishedA.snapshot.scientific_binding).toEqual(acceptedA.snapshot.scientific_binding);
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    await testInfo.attach("A-B identities", {
      body: JSON.stringify(
        { previewA, previewB, receiptA, receiptB, runA, procedureA, procedureB },
        null,
        2,
      ),
      contentType: "application/json",
    });
    // A historical selection must survive an ordinary page round trip even after B was admitted.
    await page.getByText("Retained procedures", { exact: true }).click();
    const historicalA = page
      .locator("details")
      .filter({ has: page.getByText("Retained procedures", { exact: true }) })
      .getByRole("button")
      .last();
    await historicalA.focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("link", { name: "Reopen this procedure", exact: true }),
    ).toHaveAttribute("href", `?procedure=${procedureA}#launch`);
    await page.getByRole("button", { name: "Help", exact: true }).click();
    await page.goBack();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await expect(
      page.getByRole("link", { name: "Reopen this procedure", exact: true }),
    ).toHaveAttribute("href", `?procedure=${procedureA}#launch`);
    await page.goBack();
    await expect(
      page.getByRole("link", { name: "Reopen this procedure", exact: true }),
    ).toHaveAttribute("href", `?procedure=${procedureB}#launch`);
    await page.goForward();
    await expect(
      page.getByRole("link", { name: "Reopen this procedure", exact: true }),
    ).toHaveAttribute("href", `?procedure=${procedureA}#launch`);
    expect(submissions).toHaveLength(2);
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await expect(resultA.getByTestId("run-detail-header")).toContainText(runA!);
    completed = true;
  } finally {
    await writeFile(release, "");
    uv(["scopecat", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved author project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
