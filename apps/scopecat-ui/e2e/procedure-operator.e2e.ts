import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

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

const ADMIT = `
import sys
import scopecat as sc
from scopecat.application.launch import LaunchRequest
project = sc.open_project(sys.argv[1])
application = project.load_application()
with project.connect() as lab:
    provider = application.launch_provider
    request = LaunchRequest(action="preview", experiment="temperature", version="1")
    preview = provider(lab, request)
    admitted = provider(lab, LaunchRequest.model_validate({
        **request.model_dump(), "action": "submit", "request_key": "browser-retained",
        "expected_request_hash": preview.request_hash, "config_source": preview.config_source,
    }))
    print(admitted.procedure_id)
`;

test("reopens an admitted procedure after restart and follows exact retained run and analysis", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-operator-e2e-"));
  try {
    const template = join(ROOT, "examples/reference_lab");
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(template, name), join(project, name), { recursive: true });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const procedureId = uv(["python", "-c", ADMIT, project]);
    uv(["scopecat", "stop", project]);
    uv([
      "scopecat",
      "start",
      project,
      "--port",
      new URL(endpoint.base_url).port,
      "--static-dir",
      resolve("dist"),
    ]);
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByText("Retained procedures", { exact: true }).click();
    await page.getByRole("button", { name: /reference_lab.launch_temperature/ }).click();
    await expect(page.getByText("Admitted — not dispatched", { exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("procedure")).toBe(procedureId);
    const reopenedScreenshot = testInfo.outputPath("operator-reopened.png");
    await page.screenshot({ path: reopenedScreenshot, fullPage: true });
    await testInfo.attach("Reopened procedure", {
      path: reopenedScreenshot,
      contentType: "image/png",
    });
    await page.getByRole("button", { name: "Dispatch existing procedure" }).click();
    await expect(page.getByRole("status").filter({ hasText: /^Completed$/ })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("status").filter({ hasText: /^Completed$/ })).toBeVisible();
    await page.getByRole("link", { name: /^Open retained run:/ }).click();
    await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
    await expect(page.getByText("Measurement data", { exact: true })).toBeVisible();

    const runScreenshot = testInfo.outputPath("operator-retained-run.png");
    await page.screenshot({ path: runScreenshot, fullPage: true });
    await testInfo.attach("Retained run", { path: runScreenshot, contentType: "image/png" });
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("channel-timing");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Start acquisition" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: /^Waiting for review$/ }),
    ).toBeVisible();
    const analysisLink = page.getByRole("link", { name: "Open analysis", exact: true });
    const href = await analysisLink.getAttribute("href");
    expect(href).toContain("run-analysis=");
    await analysisLink.click();
    await expect(
      page.getByRole("heading", { name: "Channel timing candidate", exact: true }),
    ).toBeVisible();
    expect(new URL(page.url()).searchParams.get("procedure")).toBeTruthy();
  } finally {
    uv(["scopecat", "stop", project]);
    await rm(project, { recursive: true, force: true });
  }
});
