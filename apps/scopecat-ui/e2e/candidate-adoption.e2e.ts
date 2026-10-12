import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 90_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(`${result.error?.message ?? ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout.trim();
}
const SEED = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
from ui_signal.analysis import PeakResult, PeakVerification
with project.authoring() as author:
    content = initial_parameters(center=4.83)
    revision = author.parameters.save(name="baseline", catalog=content.catalog, parameters=content.parameters)
    author.parameters.create_branch("chip/daily", revision=revision)
    author.use(parameter_branch="chip/daily", setup=author.setup.get("initial"))
    run = author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9, 5.0]}).run().wait().result()
    fit = author.analyze_as(run.id, "ui_signal.analysis:estimate_peak", PeakResult)
    candidate = author.config.stage(fit, name="carrier", table="signal", key="signal", fields={"center": "frequency"})
    check_run = author.prepare("signal", candidate=candidate, scans={"frequency": [4.7, 4.8, 4.9, 5.0]}).run().wait().result()
    check = author.analyze_as(check_run.id, "ui_signal.analysis:verify_peak", PeakVerification, arguments={"expected_frequency": sc.Quantity(4.8, "GHz")})
    verified = candidate.verify(check)
    print(json.dumps({"run": run.id, "verification": verified.verification.id}))
`;
test("reviews an exact candidate and recovers adoption after a lost committed response", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-adoption-"));
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const seed = JSON.parse(uv(["python", "-c", SEED, project])) as {
      run: string;
      verification: string;
    };
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(seed.run)}#runs`);
    await page.getByRole("button", { name: "Review adoption and records" }).click();
    await page.getByLabel("Target parameter branch").selectOption("chip/daily");
    await expect(page.getByText("Matches original")).toBeVisible();
    await page.getByLabel("Independent verification").selectOption(`${seed.verification}/decision`);
    await page
      .getByLabel("Adoption review", { exact: true })
      .screenshot({ path: testInfo.outputPath("adoption-review.png") });
    const submissions: unknown[] = [];
    await page.route("**/api/v1/parameters/branch-publications", async (route) => {
      submissions.push(route.request().postDataJSON());
      const result = await route.fetch();
      expect(result.status()).toBe(200);
      if (submissions.length === 1) await route.abort("failed");
      else await route.fulfill({ response: result });
    });
    await page.getByRole("button", { name: "Adopt verified candidate into chip/daily" }).click();
    await expect(page.getByText("The local daemon did not respond.")).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Review adoption and records" }).click();
    await page.getByRole("button", { name: "Recover original adoption" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Adopted into chip/daily" }),
    ).toBeVisible();
    expect(submissions).toHaveLength(2);
    expect(submissions[1]).toEqual(submissions[0]);
    const history = await (
      await page.request.get(`${endpoint.base_url}/api/v1/parameters/branch-history/chip/daily`)
    ).json();
    expect(history.items).toHaveLength(2);
    expect(history.items[0].generation).toBe(2);
    await page.reload();
    await page.getByRole("button", { name: "Review adoption and records" }).click();
    await page.getByLabel("Target parameter branch").selectOption("chip/daily");
    await expect(page.getByText("Already matches candidate")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Adopt verified candidate into chip/daily" }),
    ).toBeDisabled();
    await testInfo.attach("adoption-record", {
      body: await page
        .getByLabel("Adoption review", { exact: true })
        .screenshot({ path: testInfo.outputPath("adoption-record.png") }),
      contentType: "image/png",
    });
    await page.getByRole("link", { name: "Open adopted verification" }).click();
    await expect(page).toHaveURL(new RegExp(`analysis=${seed.verification}#analyses`));
    passed = true;
  } finally {
    uv(["scopecat", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved candidate project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
