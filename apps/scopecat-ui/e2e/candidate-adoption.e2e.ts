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
  page.setDefaultTimeout(10_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-adoption-"));
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
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
    const seed = JSON.parse(uv(["python", "-c", SEED, project])) as {
      run: string;
      verification: string;
    };
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(seed.run)}#runs`);
    await page.getByRole("button", { name: "审阅发布与记录" }).click();
    await page.getByLabel("目标参数分支").selectOption("chip/daily");
    await expect(page.getByText("与原始值一致")).toBeVisible();
    await page.getByLabel("独立验证").selectOption(`${seed.verification}/decision`);
    await page
      .getByLabel("发布审阅", { exact: true })
      .screenshot({ path: testInfo.outputPath("adoption-review.png") });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 844 });
      const review = page.getByLabel("发布审阅", { exact: true });
      await expect(review.getByRole("columnheader", { name: "原始值", exact: true })).toBeVisible();
      await expect(
        review.getByRole("columnheader", { name: "当前分支", exact: true }),
      ).toBeVisible();
      await expect(review.getByRole("columnheader", { name: "候选值", exact: true })).toBeVisible();
      await page.getByLabel("独立验证").focus();
      await page.keyboard.press("Tab");
      await expect(review.getByRole("link").filter({ hasText: "decision" })).toBeFocused();
      expect(
        await page.locator("html").evaluate((element) => element.scrollWidth),
      ).toBeLessThanOrEqual(width);
      await review.screenshot({ path: testInfo.outputPath(`adoption-review-${width}.png`) });
    }
    await page
      .getByLabel("发布审阅", { exact: true })
      .getByLabel("操作人", { exact: true })
      .fill("browser-author");
    const submissions: unknown[] = [];
    await page.route("**/api/v1/parameters/branch-publications", async (route) => {
      submissions.push(route.request().postDataJSON());
      const result = await route.fetch();
      expect(result.status()).toBe(200);
      if (submissions.length === 1) await route.abort("failed");
      else await route.fulfill({ response: result });
    });
    await page.getByRole("button", { name: "将已验证候选发布到 chip/daily" }).click();
    await expect(page.getByText("The local daemon did not respond.")).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "审阅发布与记录" }).click();
    await page.getByRole("button", { name: "恢复原发布请求" }).click();
    await expect(page.getByRole("status").filter({ hasText: "已发布到 chip/daily" })).toBeVisible();
    expect(submissions).toHaveLength(2);
    expect(submissions[1]).toEqual(submissions[0]);
    const history = await (
      await page.request.get(`${endpoint.base_url}/api/v1/parameters/branch-history/chip/daily`)
    ).json();
    expect(history.items).toHaveLength(2);
    expect(history.items[0].generation).toBe(2);
    await page.reload();
    await page.getByRole("button", { name: "审阅发布与记录" }).click();
    await page.getByLabel("目标参数分支").selectOption("chip/daily");
    await expect(page.getByText("已与候选值一致")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "将已验证候选发布到 chip/daily" }),
    ).toBeDisabled();
    await testInfo.attach("adoption-record", {
      body: await page
        .getByLabel("发布审阅", { exact: true })
        .screenshot({ path: testInfo.outputPath("adoption-record.png") }),
      contentType: "image/png",
    });
    await page.getByRole("link", { name: "查看发布所用验证" }).click();
    await expect(page).toHaveURL(new RegExp(`analysis=${seed.verification}#analyses`));
    passed = true;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved candidate project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
