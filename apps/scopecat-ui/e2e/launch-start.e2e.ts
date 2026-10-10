import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
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
  try {
    await copyAuthorWorkspace(project);
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    prepareAuthorContexts(uv, project);
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
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
    ).toContainText("does not publish parameter changes");
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
        await page.getByRole("button", { name: "Configuration", exact: true }).click();
      }
      release();
      await delivered;
      await page.unroute("**/experiment-launcher/preview");
      if (action === "navigate")
        await page.getByRole("button", { name: "Experiments", exact: true }).click();
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
    const preview = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/preview"),
    );
    const submitted = page.waitForRequest((request) =>
      request.url().endsWith("/experiment-launcher/submit"),
    );
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const checked = await (await preview).json();
    const request = (await submitted).postDataJSON();
    expect(request.expected_request_hash).toBe(checked.request_hash);
    expect(request.manual_state).toEqual(checked.manual_state);
    expect(request.code_revision).toEqual(checked.code_revision);
    expect(request.reviewed).toEqual(checked.reviewed);
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    expect(await runs()).toHaveLength(1);
    await page.screenshot({ path: testInfo.outputPath("started-once.png") });
  } finally {
    uv(["scopecat", "stop", project]);
    await rm(project, { recursive: true, force: true });
  }
});
