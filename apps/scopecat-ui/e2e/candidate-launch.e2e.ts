import { spawn, spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { reviewRetainedExperiment } from "./author-context";
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
    print(json.dumps({"run": run.id}))
`;
test("opens an unpublished candidate, saves a draft, and explicitly starts a linked run", async ({
  page,
}) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(10_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-candidate-launch-"));
  let daemon: ReturnType<typeof spawn> | undefined;
  let stopped: Promise<void> | undefined;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    daemon = spawn(
      uv(["python", "-c", "import sys; print(sys.executable)"]),
      [
        "-m",
        "scopecat_server.cli",
        "serve",
        project,
        "--port",
        "0",
        "--static-dir",
        resolve("dist"),
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    daemon.stdout?.resume();
    let logs = "";
    daemon.stderr?.on("data", (chunk) => {
      logs += String(chunk);
    });
    stopped = new Promise<void>((done) => daemon!.once("exit", () => done()));
    let endpoint = { base_url: "" };
    await expect
      .poll(async () => {
        if (daemon!.exitCode !== null) throw new Error(logs);
        try {
          endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8"));
          return (await page.request.get(`${endpoint.base_url}/api/v1/health`)).ok();
        } catch {
          return false;
        }
      })
      .toBe(true);
    const seed = JSON.parse(uv(["python", "-c", SEED, project]));
    const historyUrl = `${endpoint.base_url}/api/v1/parameters/branch-history/chip/daily`;
    const historyBefore = await (await page.request.get(historyUrl)).json();
    const selectionUrl = `${endpoint.base_url}/api/v1/runs/${seed.run}/parameter-proposals/carrier/launch-selection`;
    const exact = await (await page.request.get(selectionUrl)).json();
    expect(exact.configuration.kind).toBe("candidate");
    let submissions = 0;
    page.on("request", (request) => {
      if (request.url().endsWith("/experiment-launcher/submit")) submissions++;
    });
    await page.goto(`${endpoint.base_url}/?run=${seed.run}#runs`);
    await page.getByRole("button", { name: "用于新实验", exact: true }).click();
    await page.getByLabel("实验类型", { exact: true }).selectOption("signal");
    await page.getByRole("button", { name: "取消返回", exact: true }).click();
    expect(submissions).toBe(0);
    await page.getByRole("button", { name: "用于新实验", exact: true }).click();
    await page.getByLabel("实验类型", { exact: true }).selectOption("signal");
    await page.getByRole("button", { name: "创建候选草稿", exact: true }).click();
    await expect(page).toHaveURL(/draft=.*#launch/);
    expect(submissions).toBe(0);
    await reviewRetainedExperiment(page);
    await page.getByLabel("操作人", { exact: true }).fill("Candidate scientist");
    await page.getByLabel("Frequency 取值来源").selectOption("range");
    await page.getByLabel("Frequency 起点").fill("4.7");
    await page.getByLabel("Frequency 终点").fill("5.0");
    await page.getByLabel("Frequency 点数").fill("4");
    const previewResponse = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/preview"),
    );
    await page.getByRole("button", { name: "预览", exact: true }).click();
    const preview = await previewResponse;
    expect(preview.status()).toBe(200);
    expect((await preview.json()).reviewed.config_source).toEqual(exact.configuration.source);
    expect(submissions).toBe(0);
    const submitted = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/submit"),
    );
    await page.getByRole("button", { name: "开始采集", exact: true }).click();
    const admission = await submitted;
    expect(admission.status()).toBe(200);
    await expect(page.getByText("experiment: 已完成", { exact: true })).toBeVisible();
    expect(submissions).toBe(1);
    const runs = await (await page.request.get(`${endpoint.base_url}/api/v1/runs`)).json();
    const newRun = runs.items.find(
      (item: { snapshot: { run_id: string } }) => item.snapshot.run_id !== seed.run,
    );
    expect(newRun).toBeDefined();
    const detail = await (
      await page.request.get(`${endpoint.base_url}/api/v1/runs/${newRun.snapshot.run_id}`)
    ).json();
    expect(detail.snapshot.config_source).toEqual(exact.configuration.source);
    expect(await (await page.request.get(historyUrl)).json()).toEqual(historyBefore);
  } finally {
    daemon?.kill("SIGTERM");
    await stopped;
    await rm(project, { recursive: true, force: true });
  }
});
