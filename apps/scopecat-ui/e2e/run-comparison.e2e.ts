import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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
const factValue = (items: { id: string; content: { value: unknown } }[], id: string) =>
  items.find((item) => item.id === id)!.content.value;
const SEED = `
import json, sys
import scopecat as sc
project=sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.signal import signal, FREQUENCY, AMPLITUDE
from ui_signal.application import save_inputs
with project.connect() as lab:
    inputs = save_inputs(lab)
    runs=[lab.run(signal.build().with_axis(sc.axis(FREQUENCY.ref, [sc.Quantity(v,"GHz") for v in (4.6,4.7,4.8,4.9,5.0)])).with_axis(sc.axis(AMPLITUDE.ref,[sc.Quantity(a,"V")])), config=inputs) for a in (.1,.08)]
    print(json.dumps([r.id for r in runs]))
`;
test("compares retained signals, saves independent results and starts edited suggestions with exact bidirectional decision provenance", async ({
  page,
}, testInfo) => {
  // Include cold acquisition, author refresh, retained actions, and daemon cleanup.
  // CI completes individual assertions within their existing limits.
  test.setTimeout(150_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-comparison-e2e-"));
  let passed = false;
  let restoredProject: string | undefined;
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
    const [primary, secondary] = JSON.parse(uv(["python", "-c", SEED, project])) as string[];
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto(`${endpoint.base_url}/#history`);
    await expect(page.getByLabel("记录集内 run 编号", { exact: true })).toHaveCount(0);
    await page.getByLabel("记录集筛选", { exact: true }).selectOption({ label: "Default records" });
    await page.getByLabel("记录集内 run 编号", { exact: true }).fill("1");
    await page.getByRole("button", { name: "查找 run", exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath("compact-data.png"), fullPage: true });
    await page.getByRole("button", { name: "打开记录集内 run #1", exact: true }).click();
    await expect(page.getByTestId("run-detail-header")).toContainText(primary);

    await page.getByRole("link", { name: "比较已有 run", exact: true }).click();
    await expect(page.getByLabel("主 run", { exact: true })).toHaveValue(primary);
    await page.getByLabel("对照 run", { exact: true }).selectOption(secondary);
    async function action(label: string) {
      await page.getByLabel("判断操作人", { exact: true }).fill("synthetic-reviewer");
      const actions: Record<string, string> = {
        检查兼容数据: "inspect",
        "拟合选中数据并保存 analysis": "fit",
        创建候选: "candidate",
        将建议输入导入准备: "handoff",
        记录候选拒绝: "reject",
      };
      const response = page.waitForResponse(
        (value) =>
          value.url().endsWith("/run-comparison") &&
          value.request().method() === "POST" &&
          value.request().postDataJSON().action === actions[label],
      );
      await page.getByRole("button", { name: label, exact: true }).click();
      const result = await response;
      expect(result.status(), await result.text()).toBe(200);
      return (await result.json()) as { analysis_id?: string; publication_hash?: string };
    }
    await action("检查兼容数据");
    await expect(
      page.getByRole("option", { name: "Default records · #1", exact: true }),
    ).toHaveCount(2);
    await expect(
      page
        .getByRole("region", { name: "已有 run 比较" })
        .locator("svg text")
        .filter({ hasText: "Default records · #1" }),
    ).toHaveCount(1);
    await page.screenshot({ path: testInfo.outputPath("comparison-dark.png"), fullPage: true });
    await page.emulateMedia({ colorScheme: "light" });
    await page.setViewportSize({ width: 800, height: 720 });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(800);
    await page.screenshot({
      path: testInfo.outputPath("comparison-light-preference.png"),
      fullPage: true,
    });
    await page.getByLabel("主 run 选点位置").fill("4,1,2,3");
    await page.goBack();
    await expect(page.getByTestId("run-detail-header")).toBeVisible();
    await page.goForward();
    await expect(page.getByLabel("对照 run", { exact: true })).toHaveValue(secondary);
    await expect(page.getByLabel("主 run 选点位置")).toHaveValue("4,1,2,3");
    await page.reload();
    await expect(page.getByLabel("对照 run", { exact: true })).toHaveValue(secondary);
    await expect(page.getByLabel("主 run 选点位置")).toHaveValue("4,1,2,3");
    await expect(
      page.getByRole("button", { name: "拟合选中数据并保存 analysis", exact: true }),
    ).toBeDisabled();
    await action("检查兼容数据");
    await expect(page.getByLabel("主 run 选点位置")).toHaveValue("4,1,2,3");
    const first = await action("拟合选中数据并保存 analysis");
    await expect(page.getByRole("button", { name: "创建候选", exact: true })).toBeVisible();
    const modelPath = join(project, "src/ui_signal/model.py");
    const source = await readFile(modelPath, "utf8");
    await writeFile(
      modelPath,
      source
        .replace('version="1"', 'version="2"')
        .replace(
          "center = -coefficients[1] / (2 * coefficients[2]) + offset_ghz",
          "center = -coefficients[1] / (2 * coefficients[2]) + offset_ghz + 0.02",
        ),
    );
    await page.getByText(/分析代码与来源详情/).click();
    const refreshed = page.waitForResponse(
      (value) =>
        value.request().method() === "POST" && value.url().endsWith("/author-preparations"),
    );
    await page.getByRole("button", { name: "刷新作者代码", exact: true }).click();
    expect((await refreshed).status()).toBe(200);
    await expect(
      page.getByRole("status").filter({ hasText: "Author code refreshed" }),
    ).toBeVisible();
    await action("检查兼容数据");
    await page.getByLabel("主 run 选点位置").fill("4,1,2,3");
    await page.getByLabel("Carrier offset (GHz)").fill("0.01");
    const second = await action("拟合选中数据并保存 analysis");
    expect(second.analysis_id).not.toBe(first.analysis_id);
    const facts = async (id: string) => {
      const response = await page.request.get(
        `${endpoint.base_url}/api/v1/runs/${primary}/analyses/${id}`,
      );
      expect(response.status()).toBe(200);
      const body = await response.json();
      return body.analysis.outputs.filter((output: { kind: string }) => output.kind === "fact");
    };
    const firstFacts = await facts(first.analysis_id!);
    const secondFacts = await facts(second.analysis_id!);
    expect(factValue(firstFacts, "fit")).toMatchObject({ model_version: "1" });
    expect(factValue(secondFacts, "fit")).toMatchObject({ model_version: "2" });
    expect(factValue(secondFacts, "comparison-request")).not.toEqual(
      factValue(firstFacts, "comparison-request"),
    );
    await page.locator("aside button").filter({ hasText: first.analysis_id }).click();
    const candidate = await action("创建候选");
    const adoptionLink = page.getByRole("link", {
      name: "检查参数变更与接纳步骤",
    });
    await expect(adoptionLink).toHaveAttribute(
      "href",
      `?run=${encodeURIComponent(primary)}&run-analysis=${encodeURIComponent(candidate.analysis_id!)}#runs`,
    );
    const comparisonUrl = page.url();
    await adoptionLink.click();
    const proposals = page.getByTestId("run-proposals-card");
    await expect(proposals).toBeVisible();
    await expect(proposals.getByText(/Collect independent verification/)).toBeVisible();
    await expect(proposals.getByRole("link", { name: "Verify a candidate" })).toBeVisible();
    await proposals.screenshot({ path: testInfo.outputPath("candidate-adoption-steps.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await proposals.evaluate((element) => {
      element.ownerDocument.defaultView?.scrollBy(0, element.getBoundingClientRect().top - 130);
    });
    await page.screenshot({ path: testInfo.outputPath("candidate-adoption-narrow.png") });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.setViewportSize({ width: 1280, height: 720 });
    const sourceAnalysis = page.getByRole("link", { name: "View source analysis", exact: true });
    await expect(sourceAnalysis).toHaveAttribute(
      "href",
      `?run=${encodeURIComponent(primary)}&run-analysis=${encodeURIComponent(candidate.analysis_id!)}#runs`,
    );
    await sourceAnalysis.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("candidate-evidence-link.png") });
    await sourceAnalysis.click();
    await expect(page.getByText(candidate.analysis_id!, { exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("run")).toBe(primary);
    expect(new URL(page.url()).searchParams.get("run-analysis")).toBe(candidate.analysis_id);
    await page.screenshot({ path: testInfo.outputPath("candidate-source-analysis.png") });
    await page.goto(comparisonUrl);
    await page.getByLabel("拒绝原因").fill("Need independent physical verification");
    await action("记录候选拒绝");
    await expect(page.getByText("独立判断已记录，不代表任务已获批准。")).toBeVisible();
    await page.locator("aside button").filter({ hasText: candidate.analysis_id }).click();
    await action("将建议输入导入准备");
    await expect(page.getByLabel("实验类型", { exact: true })).toHaveValue("ui_signal.signal");
    expect(
      Math.abs(Number(await page.getByLabel("Frequency", { exact: true }).inputValue()) - 4.8),
    ).toBeLessThan(0.02);
    await expect(
      page.getByText(/The exact decision source and submitted inputs will be retained/),
    ).toBeVisible();
    const retained = await page.request.get(`${endpoint.base_url}/api/v1/runs?limit=100`);
    expect((await retained.json()).items).toHaveLength(2);
    await page.getByLabel("Frequency", { exact: true }).fill("4.87");
    await page.getByRole("textbox", { name: "操作人", exact: true }).fill("synthetic-reviewer");
    let submissions = 0;
    let procedure = "";
    await page.route("**/experiment-launcher/submit", async (route) => {
      submissions++;
      const command = route.request().postDataJSON();
      expect(command.plan_ref ?? null).toBeNull();
      expect(command.decision_source).toEqual({
        run_id: primary,
        analysis_id: candidate.analysis_id,
        publication_hash: candidate.publication_hash,
      });
      const response = await route.fetch();
      expect(response.status(), await response.text()).toBe(200);
      procedure = (await response.json()).procedure_id;
      await route.abort("connectionfailed");
    });
    // No separate Preview and no named plan: Start checks and admits the edited input.
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "开始采集", exact: true }).click();
    await page.getByRole("button", { name: "核实原始提交", exact: true }).click();
    await page.getByRole("button", { name: "查看已提交任务", exact: true }).click();
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(`${endpoint.base_url}/api/v1/procedures/${procedure}/operator`)
            ).json()
          ).procedure.state,
      )
      .toBe("closed");
    const launched = (
      await (await page.request.get(`${endpoint.base_url}/api/v1/runs?limit=100`)).json()
    ).items;
    expect(launched).toHaveLength(3);
    expect(submissions).toBe(1);
    const nextRun = launched[0].control.admission.run_id as string;
    const accepted = await (
      await page.request.get(`${endpoint.base_url}/api/v1/runs/${nextRun}/request`)
    ).json();
    expect(accepted.request.decision_source).toEqual({
      run_id: primary,
      analysis_id: candidate.analysis_id,
      publication_hash: candidate.publication_hash,
    });
    expect(JSON.stringify(accepted.request)).toContain("4.87");
    await page.goto(`${endpoint.base_url}/?run=${nextRun}#runs`);
    const documentIdentity = await page.evaluate(() => performance.timeOrigin);
    await page.getByRole("link", { name: "Open exact source conclusion" }).click();
    await expect(page).toHaveURL(new RegExp(`run-analysis=${candidate.analysis_id}`));
    expect(await page.evaluate(() => performance.timeOrigin)).toBe(documentIdentity);
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`run=${nextRun}#runs`));
    await expect(page.getByRole("region", { name: "Decision source" })).toBeVisible();
    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`run-analysis=${candidate.analysis_id}`));
    expect(await page.evaluate(() => performance.timeOrigin)).toBe(documentIdentity);
    const followups = page.getByRole("region", {
      name: "Experiments started from this conclusion",
    });
    await followups.getByRole("button").filter({ hasText: "#3" }).click();
    await expect(page).toHaveURL(new RegExp(`run=${nextRun}`));
    const exported = await page.request.get(
      `${endpoint.base_url}/api/v1/data/runs/${nextRun}/file`,
    );
    expect(exported.status(), await exported.text()).toBe(200);
    const capture = join(project, "decision-source.scopecat");
    await writeFile(capture, await exported.body());
    uv([
      "python",
      "-c",
      `
from scopecat.data_exchange import ScientificExchange
import sys
with ScientificExchange(sys.argv[1]) as capture:
    capture.verify()
    target = next(r for r in capture.evidence.runs if r.snapshot.run_id == sys.argv[2])
    source = target.request.decision_source
    assert source.analysis_id == sys.argv[3]
    assert any(a.entry.id == source.analysis_id and a.record.publication_hash == source.publication_hash for a in capture.evidence.analyses)
    assert {sys.argv[4], sys.argv[5]} <= {r.snapshot.run_id for r in capture.evidence.runs}
    for run_id in (sys.argv[4], sys.argv[5]):
        recording = capture.recording(run_id)
        assert recording.header.run_id == run_id
        recording.verify()
        assert len(tuple(recording.records())) == 5
    assert capture.payloads
    for payload in capture.payloads:
        assert len(capture.read_payload(payload)) == payload.size
`,
      capture,
      nextRun,
      candidate.analysis_id!,
      primary,
      secondary,
    ]);
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
    // Exercise bounded rendering with a large retained-value response, without changing stored evidence.
    const longRows = Array.from({ length: 1000 }, (_, id) => ({ id, value: "x".repeat(100) }));
    await page.route(`**/api/v1/runs/${primary}/parameter-proposals?*`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const delta = body.items[0].proposal.deltas[0];
      delta.cells = null;
      delta.after = { id: delta.parameter_id, shape: "table", rows: longRows };
      await route.fulfill({ response, json: body });
    });
    await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(primary)}#runs`);
    const longProposals = page.getByTestId("run-proposals-card");
    await longProposals.getByText("View full value", { exact: true }).click();
    const fullValue = longProposals.getByLabel("Full parameter value");
    await expect(fullValue).toHaveText(JSON.stringify(longRows));
    expect(await fullValue.evaluate((element) => element.clientHeight)).toBeLessThanOrEqual(192);
    expect(await fullValue.evaluate((element) => element.scrollHeight)).toBeGreaterThan(192);
    await longProposals.screenshot({ path: testInfo.outputPath("bounded-full-value.png") });
    // Stopped current-format backup restores the same exact source and reverse index.
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    const snapshot = `${project}-snapshot`;
    restoredProject = `${project}-restored`;
    uv(["python", "-m", "scopecat_server.cli", "snapshot", "create", project, snapshot]);
    uv(["python", "-m", "scopecat_server.cli", "snapshot", "restore", snapshot, restoredProject]);
    uv([
      "python",
      "-m",
      "scopecat_server.cli",
      "start",
      restoredProject,
      "--port",
      "0",
      "--static-dir",
      resolve("dist"),
    ]);
    const restored = JSON.parse(
      await readFile(join(restoredProject, ".scopecat/daemon.json"), "utf8"),
    ) as { base_url: string };
    await page.goto(`${restored.base_url}/?run=${nextRun}#runs`);
    await page.getByRole("link", { name: "Open exact source conclusion" }).click();
    await expect(page).toHaveURL(new RegExp(`run-analysis=${candidate.analysis_id}`));
    await page
      .getByRole("region", { name: "Experiments started from this conclusion" })
      .getByRole("button")
      .filter({ hasText: "#3" })
      .click();
    await expect(page).toHaveURL(new RegExp(`run=${nextRun}`));
    await page
      .locator("html")
      .evaluate((element) => element.ownerDocument.defaultView?.scrollTo(0, 0));
    await page.screenshot({
      path: testInfo.outputPath("restored-decision-source.png"),
      fullPage: true,
    });
    expect(
      (await (await page.request.get(`${restored.base_url}/api/v1/runs?limit=100`)).json()).items,
    ).toHaveLength(3);
    await rm(snapshot, { recursive: true, force: true });
    passed = true;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", restoredProject ?? project]);
    if (restoredProject && passed) await rm(restoredProject, { recursive: true, force: true });
    if (passed) await rm(project, { recursive: true, force: true });
    else
      await testInfo.attach("Preserved comparison project", {
        body: project,
        contentType: "text/plain",
      });
  }
});
