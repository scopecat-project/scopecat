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
const SEED = `
import json, sys
import scopecat as sc
project=sc.open_project(sys.argv[1])
project.load_application()
from reference_lab.workflows.frequency_amplitude import frequency_amplitude, FREQUENCY, AMPLITUDE
with project.connect() as lab:
    runs=[lab.run(frequency_amplitude().with_axis(sc.axis(FREQUENCY.ref, [sc.Quantity(v,"GHz") for v in (4.6,4.7,4.8,4.9,5.0)])).with_axis(sc.axis(AMPLITUDE.ref,[sc.Quantity(a,"V")]))) for a in (.1,.08)]
    print(json.dumps([r.id for r in runs]))
`;
test("compares retained signals, saves independent results and imports a reviewed suggestion without acquisition", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-comparison-e2e-"));
  let passed = false;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const [primary, secondary] = JSON.parse(uv(["python", "-c", SEED, project])) as string[];
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(primary)}#runs`);
    await page.getByRole("link", { name: "Compare retained runs", exact: true }).click();
    await expect(page.getByLabel("Primary run")).toHaveValue(primary);
    await page.getByLabel("Secondary run").selectOption(secondary);
    async function action(label: string) {
      const response = page.waitForResponse(
        (value) => value.url().endsWith("/run-comparison") && value.request().method() === "POST",
      );
      await page.getByRole("button", { name: label, exact: true }).click();
      const result = await response;
      expect(result.status(), await result.text()).toBe(200);
      return (await result.json()) as { analysis_id?: string; publication_hash?: string };
    }
    await action("Inspect compatible data");
    await page.getByLabel("Primary selected positions").fill("4,1,2,3");
    const first = await action("Fit selected data and save analysis");
    await expect(
      page.getByRole("button", { name: "Create explicit candidate", exact: true }),
    ).toBeVisible();
    await page.getByLabel("Carrier offset (GHz)").fill("0.01");
    const second = await action("Fit selected data and save analysis");
    expect(second.analysis_id).not.toBe(first.analysis_id);
    await page.locator("aside button").filter({ hasText: first.analysis_id }).click();
    const candidate = await action("Create explicit candidate");
    await page.getByLabel("Rejection reason").fill("Need independent physical verification");
    await action("Record candidate rejection");
    await expect(
      page.getByText("Independent review recorded. This is not procedure approval."),
    ).toBeVisible();
    await page.locator("aside button").filter({ hasText: candidate.analysis_id }).click();
    await action("Import suggested inputs into Launch");
    await expect(page.getByLabel("Experiment", { exact: true })).toHaveValue("frequency-amplitude");
    expect(
      Math.abs(Number(await page.getByLabel("Frequency", { exact: true }).inputValue()) - 4.8),
    ).toBeLessThan(0.02);
    await expect(
      page.getByText(/Source provenance is retained only in this console draft/),
    ).toBeVisible();
    const retained = await page.request.get(`${endpoint.base_url}/api/v1/runs?limit=100`);
    expect((await retained.json()).items).toHaveLength(2);
    await page.goto(
      `${endpoint.base_url}/?compare=${encodeURIComponent(primary)}&comparison-analysis=${encodeURIComponent(first.analysis_id!)}#analyses`,
    );
    await expect(page.getByRole("heading", { name: /Signal fit.*revision 1/ })).toBeVisible();
    const screenshot = testInfo.outputPath("retained-comparison.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach("Retained comparison and history", {
      path: screenshot,
      contentType: "image/png",
    });
    passed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved comparison project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
