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
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await expect(page.getByLabel("Frequency", { exact: true })).toHaveValue("4.8");
    await page.getByLabel("Gain", { exact: true }).fill("2");
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
    expect(await preview.json()).toMatchObject({
      experiment_id: "signal",
      point_count: 3,
      controls: expect.arrayContaining([
        expect.objectContaining({ id: "frequency", state: "scanned" }),
      ]),
    });
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/submit") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const admitted = await submitted;
    expect(admitted.status()).toBe(200);
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
