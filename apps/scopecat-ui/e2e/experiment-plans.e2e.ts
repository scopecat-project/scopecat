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
    throw new Error(`${result.error?.message ?? ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout.trim();
}
test("saves, reopens, copies and submits an immutable plan without activating configuration", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-plans-e2e-"));
  let passed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const { base_url: endpoint } = JSON.parse(
      await readFile(join(project, ".scopecat/daemon.json"), "utf8"),
    ) as { base_url: string };
    await page.goto(`${endpoint}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("frequency-amplitude");
    await page.getByLabel("Operator", { exact: true }).fill("alice");
    async function preview() {
      const response = page.waitForResponse((r) =>
        r.url().endsWith("/experiment-launcher/preview"),
      );
      await page.getByRole("button", { name: "Preview", exact: true }).click();
      const result = await response;
      expect(result.status(), await result.text()).toBe(200);
    }
    async function save(label: string) {
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/experiment-plans") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: label, exact: true }).click();
      const result = await response;
      expect(result.status(), await result.text()).toBe(200);
      return await result.json();
    }
    await preview();
    await page.getByLabel("Plan name").fill("Signal baseline");
    const first = await save("Save plan");
    expect(
      (await (await page.request.get(`${endpoint}/api/v1/runs?limit=100`)).json()).items,
    ).toHaveLength(0);
    await page.reload();
    await page.getByRole("button", { name: "Open Signal baseline r1", exact: true }).click();
    await expect(page.getByLabel("Plan name")).toHaveValue("Signal baseline");
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    await page.getByLabel("Operator", { exact: true }).fill("bob");
    await preview();
    await page.getByLabel("Plan name").fill("Signal copy");
    const copied = await save("Save as copy");
    expect(copied.ref.plan_id).not.toBe(first.ref.plan_id);
    expect(copied.copied_from).toEqual(first.ref);
    expect(copied.saved_by).toBe("bob");
    await preview();
    const receipt = page.waitForResponse((r) => r.url().endsWith("/experiment-launcher/submit"));
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const submitted = await receipt;
    expect(submitted.status(), await submitted.text()).toBe(200);
    await expect(page.getByRole("link", { name: "Reopen exact plan" })).toBeVisible();
    const retained = await page.request.post(`${endpoint}/api/v1/experiment-plans/read`, {
      data: first.ref,
    });
    expect(await retained.json()).toEqual(first);
    await page.screenshot({ path: testInfo.outputPath("plan-origin.png"), fullPage: true });
    passed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved plan project", { body: project, contentType: "text/plain" });
  }
});
