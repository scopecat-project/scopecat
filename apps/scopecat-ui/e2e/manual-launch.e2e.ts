import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
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

test("direct device pages keep separate contexts and share ownership", async ({
  page,
  context,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-device-context-e2e-"));
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
    const second = await context.newPage();
    await page.goto(`${endpoint.base_url}/#instruments`);
    await second.goto(`${endpoint.base_url}/#instruments`);
    await page.getByLabel("Device context", { exact: true }).selectOption("browser-bench-a");
    await second.getByLabel("Device context", { exact: true }).selectOption("browser-bench-b");
    await page.getByTitle("Inspect instrument drive-lo-a").click();
    await second.getByTitle("Inspect instrument drive-lo-a").click();
    const opening = page.waitForRequest((request) =>
      request.url().endsWith("/instrument-sessions"),
    );
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    expect((await opening).postDataJSON().setup.revision_id).toBe("browser-bench-a");
    await expect(page.getByText("Interactive session connected")).toBeVisible();
    await expect(page.getByLabel("Device context", { exact: true })).toBeDisabled();
    await second.getByRole("button", { name: "Refresh instruments", exact: true }).click();
    await expect(second.getByRole("button", { name: "Connect", exact: true })).not.toBeVisible();
    await expect(second.getByLabel("Device context", { exact: true })).toHaveValue(
      "browser-bench-b",
    );
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await expect(page.getByLabel("Device context", { exact: true })).toBeEnabled();
    await second.getByRole("button", { name: "Refresh instruments", exact: true }).click();
    const nextOpening = second.waitForRequest((request) =>
      request.url().endsWith("/instrument-sessions"),
    );
    await second.getByRole("button", { name: "Connect", exact: true }).click();
    expect((await nextOpening).postDataJSON().setup.revision_id).toBe("browser-bench-b");
    await expect(second.getByText("Interactive session connected")).toBeVisible();
    await second.getByRole("button", { name: "Disconnect", exact: true }).click();
    await expect(second.getByLabel("Device context", { exact: true })).toBeEnabled();
    await second.close();
    completed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved device context project", {
        body: project,
        contentType: "text/plain",
      });
  }
});

test("manual changes invalidate a retained preview before a fresh acquisition", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-manual-e2e-"));
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
    await page.getByLabel("Experiment", { exact: true }).selectOption("ramsey");
    await chooseReferenceContext(page);
    await page.getByLabel("Delay", { exact: true }).fill("64");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeEnabled();
    uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
from scopecat.application import LabApplication
from scopecat_instruments import rf_source
with LabApplication().connect(sys.argv[1]) as lab:
    target = rf_source("drive-lo-a")
    with lab.instruments.open(target, setup=lab.setup.active().revision.ref) as devices:
        observed = devices[target].frequency.read_observation()
        assert observed.source == "hardware_query"
        assert devices[target].apply(frequency=sc.Quantity(4.95, "GHz")).status == "applied"
`,
      endpoint.base_url,
    ]);
    await expect(page.getByText(/Manual instrument changes invalidate this preview/)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await expect(page.getByLabel("Delay", { exact: true })).toHaveValue("64");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    const submitted = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/submit"),
    );
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    expect((await submitted).status()).toBe(200);
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: /^Open retained run:/ }).click();
    await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
    completed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (completed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved manual project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
