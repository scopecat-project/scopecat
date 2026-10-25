import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { chooseAuthorContext, copyAuthorWorkspace, prepareAuthorContexts } from "./author-context";

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

test("Start prepares internally while cancelled and edited preparations never acquire", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-start-e2e-"));
  const releasePath = join(project, "release-second-point");
  try {
    await copyAuthorWorkspace(project);
    // Hold the second synthetic point so the browser can open the running acquisition.
    const sourcePath = join(project, "src/ui_author/signal.py");
    await writeFile(
      sourcePath,
      (await readFile(sourcePath, "utf8")).replace(
        "    detuning =",
        `    if frequency.to("GHz").value > 4.8:\n        import time\n        from pathlib import Path\n        deadline = time.monotonic() + 60\n        while not Path(${JSON.stringify(releasePath)}).exists():\n            if time.monotonic() > deadline:\n                raise TimeoutError("Browser did not release second point")\n            time.sleep(0.05)\n    detuning =`,
      ),
    );
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
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    prepareAuthorContexts(uv, project);
    await page.goto(`${endpoint.base_url}/#settings`);
    await page.getByLabel("Operator name", { exact: true }).fill("Browser operator");
    await page.getByRole("button", { name: "Save operator", exact: true }).click();
    await expect(page.getByText("Operator saved", { exact: true })).toBeVisible();
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    await expect(page.locator('input[aria-label="Operator"]')).toHaveValue("Browser operator");
    let previewRequests = 0;
    page.on("request", (request) => {
      if (request.url().endsWith("/experiment-launcher/preview")) previewRequests += 1;
    });
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "Recheck device connections", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Recheck device connections", exact: true }),
    ).toBeEnabled();
    expect(previewRequests).toBe(0);
    await page.getByRole("button", { name: "Browse experiments", exact: true }).click();
    await page.getByRole("searchbox", { name: "Search experiments" }).fill("synthetic resonance");
    await expect(page.getByRole("button", { name: "Prepare Exploratory signal" })).toBeDisabled();
    await expect(page.getByLabel("Experiment", { exact: true })).toHaveValue("signal");
    await page.screenshot({ path: testInfo.outputPath("browse-experiments.png"), fullPage: true });
    await page
      .getByRole("searchbox", { name: "Search experiments" })
      .fill("no matching experiment");
    await expect(page.getByText(/No matching experiments/)).toBeVisible();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("1");
    await page.getByRole("button", { name: "Close experiment browser" }).click();
    await expect(
      page.getByRole("region", { name: "Selected experiment", exact: true }),
    ).not.toContainText("does not publish parameter changes");
    await expect(page.getByRole("region", { name: "Parameter source", exact: true })).toContainText(
      "no overrides",
    );
    await expect(
      page.getByRole("region", { name: "Inputs for this run", exact: true }),
    ).toContainText("does not publish a parameter version");
    await page.setViewportSize({ width: 390, height: 844 });
    await page
      .getByRole("region", { name: "Preview and start", exact: true })
      .scrollIntoViewIfNeeded();
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeInViewport();
    await expect(page.getByRole("button", { name: "Preview", exact: true })).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath("prepare-mobile.png") });
    await page.setViewportSize({ width: 1280, height: 900 });
    const runs = async () =>
      (await (await page.request.get(`${endpoint.base_url}/api/v1/runs`)).json()).items;
    for (const action of ["cancel", "edit", "navigate"]) {
      let release!: () => void;
      const held = new Promise<void>((done) => {
        release = done;
      });
      let ready!: () => void;
      const received = new Promise<void>((done) => {
        ready = done;
      });
      let finished!: () => void;
      const delivered = new Promise<void>((done) => {
        finished = done;
      });
      await page.route("**/experiment-launcher/preview", async (route) => {
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        ready();
        await held;
        await route.fulfill({ response });
        finished();
      });
      await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
      await received;
      await expect(page.getByRole("button", { name: "Preparing acquisition…" })).toBeDisabled();
      await expect(page.getByText(/Acquisition has not been submitted/)).toBeVisible();
      if (action === "cancel") {
        await page.screenshot({ path: testInfo.outputPath("preparing-start.png") });
        await page.getByRole("button", { name: "Cancel preparation" }).click();
      } else if (action === "edit") {
        await page.getByLabel("Gain", { exact: true }).fill("2");
      } else {
        await page.getByRole("button", { name: /Configuration$/, exact: true }).click();
      }
      release();
      await delivered;
      await page.unroute("**/experiment-launcher/preview");
      if (action === "navigate")
        await page.getByRole("button", { name: /Experiments$/, exact: true }).click();
      await expect(
        page.getByRole("button", { name: "Start acquisition", exact: true }),
      ).toBeEnabled();
      await expect(page.getByText("Preview ready", { exact: true })).toHaveCount(0);
      expect(await runs()).toHaveLength(0);
    }
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    expect(await runs()).toHaveLength(0);
    await page.getByLabel("Gain", { exact: true }).fill("3");
    await expect(page.getByText("Preview ready", { exact: true })).toHaveCount(0);
    await page.getByLabel("Frequency source").selectOption("range");
    await page.getByLabel("Frequency start").fill("4.8");
    await page.getByLabel("Frequency stop").fill("4.9");
    await page.getByLabel("Frequency points").fill("2");
    const preview = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/preview"),
    );
    const submitted = page.waitForRequest((request) =>
      request.url().endsWith("/experiment-launcher/submit"),
    );
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const checked = await (await preview).json();
    const request = (await submitted).postDataJSON();
    expect(request.control_edits.gain).toEqual({ mode: "fixed", value: 3 });
    expect(request.expected_request_hash).toBe(checked.request_hash);
    expect(request.manual_state).toEqual(checked.manual_state);
    expect(request.code_revision).toEqual(checked.code_revision);
    expect(request.reviewed).toEqual(checked.reviewed);
    const current = page.getByRole("link", { name: /View current data:/ });
    await expect(current).toBeVisible();
    const currentHref = await current.getAttribute("href");
    const runId = new URL(currentHref!, endpoint.base_url).searchParams.get("run");
    const procedureId = new URL(currentHref!, endpoint.base_url).searchParams.get("procedure");
    await current.click();
    await expect(page.getByTestId("run-status")).toHaveText("Running");
    await expect(
      page.getByTestId("data-card").getByText("Measurement data", { exact: true }),
    ).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("current-data.png"), fullPage: true });
    await writeFile(releasePath, "release");
    // The current view receives the completed batch without reopening or collecting again.
    await expect(page.getByTestId("data-card").getByText(/^2 records/)).toBeVisible();
    await page.getByRole("link", { name: "Return to preparation", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "Prepare an experiment", exact: true }),
    ).toBeFocused();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await expect(page.getByLabel("Frequency points")).toHaveValue("2");
    await expect(
      page.getByRole("region", { name: "Selected execution", exact: true }),
    ).toBeHidden();
    await page.getByRole("button", { name: "Monitor", exact: true }).click();
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Submitted work", exact: true }).click();
    await page.getByRole("button", { name: /signal.*Completed/ }).click();
    expect(new URL(page.url()).searchParams.get("procedure")).toBe(procedureId);
    const saved = page.getByRole("link", { name: /View saved data:/ });
    expect(
      new URL((await saved.getAttribute("href"))!, endpoint.base_url).searchParams.get("run"),
    ).toBe(runId);
    await saved.click();
    await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
    await expect(page.getByTestId("data-card").getByText(/^2 records/)).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("historical-data.png"), fullPage: true });
    await page.getByRole("link", { name: "Return to preparation", exact: true }).click();
    await expect(
      page.getByText(
        "Showing your current editable inputs. Returning from a run does not load its historical inputs.",
      ),
    ).toBeVisible();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("3");
    await expect(page.getByLabel("Frequency points")).toHaveValue("2");
    await page.getByLabel("Gain", { exact: true }).fill("4");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Prepare another acquisition", exact: true }).click();
    await expect(page.getByLabel("Gain", { exact: true })).toHaveValue("4");
    await expect(page.getByLabel("Frequency points")).toHaveValue("2");
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeEnabled();
    expect(await runs()).toHaveLength(1);
    await page.screenshot({
      path: testInfo.outputPath("adjust-without-acquisition.png"),
      fullPage: true,
    });
    // Explicit Start acquires again, while the existing monitor remains bound to A.
    const nextSubmission = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/submit"),
    );
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const nextResponse = await nextSubmission;
    expect(nextResponse.request().postDataJSON().control_edits.gain).toEqual({
      mode: "fixed",
      value: 4,
    });
    const nextProcedure = (await nextResponse.json()).procedure_id;
    expect(nextProcedure).not.toBe(procedureId);
    expect(new URL(page.url()).searchParams.get("procedure")).toBe(procedureId);
    await expect(
      page.getByRole("region", { name: "Selected execution", exact: true }),
    ).toBeHidden();
    await page.getByRole("button", { name: "Open submitted procedure", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "Selected execution", exact: true }),
    ).toBeFocused();
    expect(new URL(page.url()).searchParams.has("preparation")).toBe(false);
    expect(new URL(page.url()).searchParams.get("procedure")).toBe(nextProcedure);
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    expect(await runs()).toHaveLength(2);
  } finally {
    await writeFile(releasePath, "release");
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    await rm(project, { recursive: true, force: true });
  }
});
