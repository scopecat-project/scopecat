import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { chooseReferenceContext, prepareReferenceContexts } from "./reference-context";

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

type RetainedProcedure = {
  project: string;
  baseUrl: string;
  procedureId: string;
};

// Keep daemon lifecycle work outside the browser assertion body. In particular,
// stopping the daemon must not turn a completed browser scenario into a timeout.
const retainedProcedureTest = test.extend<{ retainedProcedure: RetainedProcedure }>({
  retainedProcedure: async ({}, use) => {
    const project = await mkdtemp(join(tmpdir(), "scopecat-operator-e2e-"));
    try {
      const template = join(ROOT, "testing/reference_lab");
      for (const name of ["src", "config", "scopecat.toml"])
        await cp(join(template, name), join(project, name), { recursive: true });
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
      const endpoint = JSON.parse(
        await readFile(join(project, ".scopecat/daemon.json"), "utf8"),
      ) as {
        base_url: string;
      };
      prepareReferenceContexts(uv, project);
      const procedureId = uv([
        "python",
        join(ROOT, "apps/scopecat-ui/e2e/fixtures/admit_procedure.py"),
        project,
      ]);
      uv(["python", "-m", "scopecat_server.cli", "stop", project]);
      uv([
        "python",
        "-m",
        "scopecat_server.cli",
        "start",
        project,
        "--port",
        new URL(endpoint.base_url).port,
        "--static-dir",
        resolve("dist"),
      ]);
      await use({ project, baseUrl: endpoint.base_url, procedureId });
    } finally {
      uv(["python", "-m", "scopecat_server.cli", "stop", project]);
      await rm(project, { recursive: true, force: true });
    }
  },
});

retainedProcedureTest(
  "reopens an admitted procedure after restart and follows its exact retained run",
  async ({ page, retainedProcedure }, testInfo) => {
    const { project, baseUrl, procedureId } = retainedProcedure;
    const endpoint = { base_url: baseUrl };
    try {
      const catalogReady = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname.endsWith("/experiment-launcher") &&
          response.request().method() === "GET",
      );
      await page.goto(`${endpoint.base_url}/#launch`);
      const catalogResponse = await catalogReady;
      expect(catalogResponse.status(), await catalogResponse.text()).toBe(200);
      await expect(page.getByLabel("实验类型", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "已提交任务", exact: true }).click();
      await page
        .getByRole("button", { name: /author:reference_lab.temperature_diagnostic/ })
        .click();
      await expect(
        page
          .getByRole("region", { name: "已提交任务检查" })
          .getByText("已接纳，尚未调度", { exact: true }),
      ).toBeVisible();
      const inspectedUrl = new URL(page.url());
      expect(inspectedUrl.searchParams.get("submitted_task")).toBe(procedureId);
      expect(inspectedUrl.searchParams.get("procedure")).toBeNull();
      await page.getByRole("button", { name: "在监看中打开", exact: true }).click();
      await expect(page.getByRole("button", { name: "监看", exact: true })).toHaveAttribute(
        "aria-current",
        "page",
      );
      expect(new URL(page.url()).searchParams.get("procedure")).toBe(procedureId);
      const reopenedScreenshot = testInfo.outputPath("operator-reopened.png");
      await page.screenshot({ path: reopenedScreenshot, fullPage: true });
      await testInfo.attach("Reopened procedure", {
        path: reopenedScreenshot,
        contentType: "image/png",
      });
      const dispatchResponse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === `/api/v1/procedures/${procedureId}/dispatch` &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "继续任务", exact: true }).click();
      const dispatch = await dispatchResponse;
      expect(dispatch.ok(), await dispatch.text()).toBe(true);
      const receipt = (await dispatch.json()) as { dispatch_error: string | null };
      expect(receipt.dispatch_error).toBeNull();
      // Observe the real child-run admission separately from its acquisition,
      // just as the source/candidate workflow below observes each durable step.
      const childRun = page.getByRole("link", { name: /^查看(当前结果|已保存结果):/ });
      await expect(childRun).toBeVisible();
      const childHref = await childRun.getAttribute("href");
      expect(childHref).toContain(`procedure=${procedureId}`);
      await expect(
        page
          .getByRole("region", { name: "当前执行", exact: true })
          .getByRole("status")
          .filter({ hasText: /^已完成$/ }),
      ).toBeVisible();
      await page.reload();
      await expect(
        page
          .getByRole("region", { name: "当前执行", exact: true })
          .getByRole("status")
          .filter({ hasText: /^已完成$/ }),
      ).toBeVisible();
      const retainedRun = page.getByRole("link", { name: /^查看已保存结果:/ });
      await expect(retainedRun).toHaveAttribute("href", childHref!);
      await retainedRun.click();
      await expect(page.getByTestId("run-status")).toHaveText("已完成");
      await expect(page.getByText("Measurement data", { exact: true })).toBeVisible();

      const runScreenshot = testInfo.outputPath("operator-retained-run.png");
      await page.screenshot({ path: runScreenshot, fullPage: true });
      await testInfo.attach("Retained run", { path: runScreenshot, contentType: "image/png" });
      // Reuse the retained acquisition for the generic procedure → analysis link.
      const runId = new URL(page.url()).searchParams.get("run")!;
      const analyzed = JSON.parse(
        uv([
          "python",
          "-c",
          `
import json, sys
import scopecat as sc
from scopecat.automation import RunOutputRef
with sc.open_project(sys.argv[1]).connect() as lab:
    from reference_lab.workflows.analysis_recovery import (
        RetainedTemperatureIntent, recovered_temperature_analysis,
    )
    handle = lab.procedures.submit(
        recovered_temperature_analysis,
        RetainedTemperatureIntent(run=RunOutputRef(run_id=sys.argv[2])),
        request_key="browser-analysis-link",
    ).resume()
    output = handle.output("summary")
    assert output.kind == "analysis"
    print(json.dumps({"procedure": handle.id, "analysis": output.analysis_record_id}))
`,
          project,
          runId,
        ]),
      ) as { procedure: string; analysis: string };
      await page.goto(`${endpoint.base_url}/?procedure=${analyzed.procedure}#launch`);
      const analysisLink = page.getByRole("link", { name: "打开 analysis", exact: true });
      await expect(analysisLink).toHaveAttribute(
        "href",
        `?procedure=${analyzed.procedure}&run-analysis=${analyzed.analysis}&run=${runId}#runs`,
      );
      await analysisLink.click();
      const linkedAnalysis = page.getByRole("region", { name: "Linked analysis", exact: true });
      await expect(
        linkedAnalysis.getByRole("heading", {
          name: /^Retained temperature summary · revision [1-9]\d*$/,
        }),
      ).toBeVisible();
      await expect(linkedAnalysis.getByTestId("publication-id")).toHaveText(analyzed.analysis);
      expect(new URL(page.url()).searchParams.get("run")).toBe(runId);
      expect(new URL(page.url()).searchParams.get("run-analysis")).toBe(analyzed.analysis);
      expect(new URL(page.url()).searchParams.get("procedure")).toBe(analyzed.procedure);
    } catch (error) {
      // Capture the live failure before fixture teardown stops the daemon.
      const url = new URL(page.url());
      const selectedId = url.searchParams.get("procedure");
      if (selectedId) {
        const operator = await page.request
          .get(`${url.origin}/api/v1/procedures/${encodeURIComponent(selectedId)}/operator`)
          .then(async (response) => ({ status: response.status(), body: await response.text() }))
          .catch((failure: unknown) => ({ error: String(failure) }));
        await testInfo.attach("Operator state before cleanup", {
          body: JSON.stringify(operator, null, 2),
          contentType: "application/json",
        });
      }
      const logs = ["daemon.log"];
      if (selectedId)
        logs.push(
          join(
            "procedure-workers",
            createHash("sha256").update(selectedId).digest("hex"),
            "worker.log",
          ),
        );
      for (const name of logs) {
        const body = await readFile(join(project, ".scopecat", name)).catch((failure: unknown) =>
          Buffer.from(`Log unavailable: ${String(failure)}`),
        );
        await testInfo.attach(name, { body, contentType: "text/plain" });
      }
      throw error;
    }
  },
);

retainedProcedureTest(
  "cancels retained work before dispatch without acquiring and keeps the cancellation on reopen",
  async ({ page, retainedProcedure }, testInfo) => {
    const { baseUrl, procedureId } = retainedProcedure;
    await page.goto(`${baseUrl}/?procedure=${procedureId}#launch`);
    await expect(
      page
        .getByRole("region", { name: "当前执行", exact: true })
        .getByRole("status")
        .filter({ hasText: /^已接纳，尚未调度$/ }),
    ).toBeVisible();
    await page
      .locator("summary")
      .filter({ hasText: /^取消等待任务$/ })
      .click();
    await expect(page.getByRole("button", { name: "取消等待任务", exact: true })).toBeDisabled();
    await page.getByLabel("取消操作人", { exact: true }).fill("browser operator");
    await page.getByLabel("取消原因", { exact: true }).fill("Cancel before acquisition");
    await page.getByRole("button", { name: "取消等待任务", exact: true }).click();
    await expect(
      page
        .getByRole("region", { name: "当前执行", exact: true })
        .getByRole("status")
        .filter({ hasText: /^已取消$/ }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page
        .getByRole("region", { name: "当前执行", exact: true })
        .getByRole("status")
        .filter({ hasText: /^已取消$/ }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "继续任务", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /^查看(当前结果|已保存结果):/ })).toHaveCount(0);
    const response = await page.request.get(`${baseUrl}/api/v1/procedures/${procedureId}/operator`);
    expect(response.ok()).toBe(true);
    expect((await response.json()).child_runs).toEqual([]);
    await testInfo.attach("Cancellation retained without acquisition", {
      body: await page.screenshot(),
      contentType: "image/png",
    });
  },
);

const CHANGE_DRAFT_CONFIG = `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    setup = lab.setup.get("browser-bench-a")
    device_id = setup.resolution.devices[0].device_id
    device = next(item for item in lab.devices.list() if item.device.id == device_id)
    revised = device.revision.content.model_copy(update={"access_aliases": (*device.revision.content.access_aliases, "browser:updated-connection")})
    lab.devices.update(device, connection=revised, note="Verify explicit setup invalidation")
`;

test("retains launch inputs across workspaces and invalidates previews without submitting", async ({
  page,
}, testInfo) => {
  let failed = false;
  const project = await mkdtemp(join(tmpdir(), "scopecat-draft-navigation-"));
  let submissions = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/experiment-launcher/submit")) submissions += 1;
  });
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "testing/reference_lab", name), join(project, name), {
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
    const sample = await page.request.post(`${endpoint.base_url}/api/v1/samples`, {
      data: {
        operation_id: "navigation-sample",
        sample_id: "sample-navigation",
        kind: "synthetic",
        actor: "fixture",
        content: { display_name: "Navigation sample" },
      },
    });
    expect(sample.status(), await sample.text()).toBe(201);
    prepareReferenceContexts(uv, project);
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("ramsey");
    await expect(page.getByLabel("实验类型", { exact: true })).toHaveValue("ramsey");
    await chooseReferenceContext(page);
    await page.getByLabel("样品 ID").fill("sample-navigation");
    await page.getByRole("textbox", { name: "操作人", exact: true }).fill("draft-author");
    await page.getByLabel("Delay source").selectOption("range");
    await page.getByLabel("Delay unit").selectOption("ns");
    await page.getByLabel("Delay start").fill("16");
    await page.getByLabel("Delay stop").fill("48");
    await page.getByLabel("Delay points").fill("3");
    for (const destination of [
      "参数与绑定 / Configuration",
      "设备与驱动 / Devices and drivers",
      "运行详情 / Runs",
    ]) {
      await page
        .getByRole("navigation", { name: "项目页面" })
        .getByRole("button", { name: destination, exact: true })
        .click();
      await page
        .getByRole("navigation", { name: "项目页面" })
        .getByRole("button", { name: "实验 / Experiments", exact: true })
        .click();
      await expect(page.getByLabel("实验类型", { exact: true })).toHaveValue("ramsey");
      await expect(page.getByLabel("样品 ID")).toHaveValue("sample-navigation");
      await expect(page.getByRole("textbox", { name: "操作人", exact: true })).toHaveValue(
        "draft-author",
      );
      await expect(page.getByLabel("Delay source")).toHaveValue("range");
      await expect(page.getByLabel("Delay unit")).toHaveValue("ns");
      await expect(page.getByLabel("Delay start")).toHaveValue("16");
      await expect(page.getByLabel("Delay stop")).toHaveValue("48");
      await expect(page.getByLabel("Delay points")).toHaveValue("3");
    }
    expect(submissions).toBe(0);
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByText("预览就绪", { exact: true })).toBeVisible();
    await page.getByLabel("Delay points").fill("2");
    await expect(page.getByText("预览就绪", { exact: true })).not.toBeVisible();
    await expect(page.getByRole("button", { name: "开始采集" })).toBeEnabled();
    expect(submissions).toBe(0);
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByText("预览就绪", { exact: true })).toBeVisible();
    uv(["python", "-c", CHANGE_DRAFT_CONFIG, project]);
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: /changed; prepare/ })).toBeVisible();
    await expect(page.getByText("预览就绪", { exact: true })).toHaveCount(0);
    // Start must recheck the changed setup and reject it without submitting.
    const rejected = page.waitForResponse((response) =>
      response.url().endsWith("/experiment-launcher/preview"),
    );
    await page.getByRole("button", { name: "开始采集" }).click();
    expect((await rejected).ok()).toBe(false);
    await expect(page.getByRole("alert").filter({ hasText: /changed; prepare/ })).toBeVisible();
    expect(submissions).toBe(0);
    expect(
      (await (await page.request.get(`${endpoint.base_url}/api/v1/runs`)).json()).items,
    ).toHaveLength(0);
    const shot = testInfo.outputPath("retained-launch-draft.png");
    await page.screenshot({ path: shot, fullPage: true });
    await testInfo.attach("Changed setup is rejected before acquisition", {
      path: shot,
      contentType: "image/png",
    });
    await page.getByRole("button", { name: "重置实验草稿" }).click();
    await expect(page.getByLabel("Delay", { exact: true })).toHaveValue("48");
    await expect(page.getByLabel("样品 ID")).toHaveValue("sample-navigation");
    await expect(page.getByRole("textbox", { name: "操作人", exact: true })).toHaveValue(
      "draft-author",
    );
    expect(submissions).toBe(0);
  } catch (error) {
    failed = true;
    await testInfo.attach("Isolated project path", { body: project, contentType: "text/plain" });
    const daemonLog = await readFile(join(project, ".scopecat/daemon.log"), "utf8").catch(
      () => "No daemon log was created.",
    );
    await testInfo.attach("Daemon log", { body: daemonLog, contentType: "text/plain" });
    throw error;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    // Preserve failed project state and logs for diagnosis.
    if (!failed) await rm(project, { recursive: true, force: true });
  }
});
