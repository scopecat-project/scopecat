import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject, uv } from "./daemon";

const PREPARE = `
import json, sys
from datetime import timedelta
import scopecat as sc
from scopecat.records.calibration_policy import CalibrationRequirement
project = sc.open_project(sys.argv[1])
project.load_application()
with project.authoring() as session:
    session.refresh()
    from my_experiment.calibration import Sensor
    from my_experiment.lesson_identity import IDENTITY
    from my_experiment.task_calibration import FinishIntent, prepare_task
    from workspace_app import initial_setup
    setup = session.setup.import_recipe(initial_setup(), name=f"{IDENTITY}-setup")
    initial = session.parameters.save(name="browser-initial", catalog=sc.parameter_catalog(IDENTITY, Sensor), parameters=sc.parameter_snapshot("browser-inputs", tables={Sensor: [Sensor(id="q0", offset=.1), Sensor(id="q1", offset=.1)]}))
    ids = []
    for suffix in ("run", "cancel"):
        identity = "browser-calibration-" + "long-task-name-" * 4 + suffix
        head = session.parameters.create_branch(identity, revision=initial)
        view = prepare_task(session, identity, FinishIntent(initial=session.parameters.resolve(initial, setup=setup), destination=head, revision_name=identity + "-result", coupling=0.0)).submit()
        ids.append(identity)
    stage = view.task.specification.plan.stages[0]
    session.calibration_checks.save_profile("browser-policy", requirements=(CalibrationRequirement(id=stage.id, scope=stage.check.scope, max_age=timedelta(hours=2)),), description="Author policy stays verbatim")
    print(json.dumps(ids))
`;

test("calibration control, evidence and logs preserve precise meaning at wide and narrow widths", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(10_000);
  const root = await mkdtemp(join(tmpdir(), "scopecat-calibration-ui-"));
  const project = join(root, "lesson");
  let started = false;
  try {
    uv([
      "python",
      "-c",
      "import sys; sys.path.insert(0, sys.argv[2]); from testing.app_support.project import create_project; create_project(sys.argv[1], topic='task-calibration')",
      project,
      ROOT,
    ]);
    const base = await startProject(project);
    started = true;
    const [task, cancelled] = JSON.parse(uv(["python", "-c", PREPARE, project])) as string[];
    await page.request.put(`${base}/api/v1/operator-settings`, {
      data: { operator: "Browser operator", revision: 0 },
    });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(`${base}/?task=${task}#launch`);
    await expect(page.getByText("作者代码已刷新。", { exact: true })).toBeVisible();
    const detail = page.getByRole("region", { name: "校准任务详情" });
    await expect(detail.getByText(/推进状态： 待启动/)).toBeVisible();
    await detail.getByLabel("任务控制原因").fill("Inspect before dispatch");
    // One display/control error response; no task mutation and no automatic retry.
    let conflicts = 0;
    await page.route("**/calibration-tasks/control", async (route) => {
      conflicts++;
      if (conflicts === 1)
        await route.fulfill({ status: 409, json: { detail: "Synthetic control conflict" } });
      else await route.continue();
    });
    await expect(detail.getByRole("button", { name: "暂停任务", exact: true })).toBeEnabled();
    await detail.getByRole("button", { name: "暂停任务", exact: true }).press("Enter");
    await expect(detail.getByText("Synthetic control conflict", { exact: true })).toBeVisible();
    expect(conflicts).toBe(1);
    await detail.getByRole("button", { name: "暂停任务", exact: true }).press("Enter");
    await expect(detail.getByText(/推进状态： 已暂停/)).toBeVisible();
    await detail.getByRole("button", { name: "恢复 / 重试接纳", exact: true }).click();
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`${base}/api/v1/calibration-tasks/${task}`);
          return (await response.json()).finalization?.closure?.status;
        },
        { timeout: 60_000 },
      )
      .toBe("succeeded");
    await expect(detail.getByText("全部计划检查已通过。", { exact: false })).toBeVisible();
    await expect(detail.getByText("检查通过本身并不验证或发布合并后的参数。")).toBeVisible();
    const stage = detail
      .locator("li")
      .filter({ has: page.getByRole("heading", { name: "q0 · 已通过", exact: true }) });
    await stage.getByText("检查适用证据： q0", { exact: true }).click();
    await stage.getByLabel("证据最大有效时长（小时）").fill("2");
    await stage.getByRole("button", { name: "检查证据： q0", exact: true }).press("Enter");
    const report = stage.getByRole("region", { name: "证据报告： q0" });
    await expect(report).toBeVisible();
    await expect(report.getByText(/评估时间/)).toBeVisible();
    await expect(report.getByRole("link", { name: "打开所选测量" })).toHaveAttribute(
      "href",
      /\?run=.+#runs/,
    );
    await stage.getByText("检查已保存的能力 profile", { exact: true }).click();
    await stage.getByRole("button", { name: "加载已保存的 profile" }).click();
    await stage
      .getByRole("combobox", { name: "能力 profile", exact: true })
      .selectOption("browser-policy");
    await stage.getByRole("button", { name: "检查已保存的 profile", exact: true }).click();
    await expect(stage.getByRole("region", { name: "已保存的能力报告" })).toContainText(
      "不代表样品整体就绪",
    );
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await page.screenshot({
        path: testInfo.outputPath(`calibration-report-${width}.png`),
        fullPage: true,
      });
    }
    await detail.getByRole("button", { name: "打开最终验证与发布", exact: true }).click();
    await page.getByText("执行诊断", { exact: true }).click();
    await page.getByText("最近的 Worker 输出", { exact: true }).click();
    await expect(page.getByText(/不作为测量结果的判定/)).toBeVisible();
    await expect(page.getByText(/读取时文件大小/)).toBeVisible();
    // Explicit presentation fixtures over a real execution: raw output, bounds and absence.
    let logState = 0;
    await page.route("**/worker-log?*", (route) =>
      route.fulfill({
        json:
          logState === 0
            ? {
                available: true,
                text: "Author raw log: <failure>",
                total_bytes: 20000,
                truncated: true,
              }
            : { available: false, text: "", total_bytes: 0, truncated: false },
      }),
    );
    await page.getByRole("button", { name: "刷新 Worker 输出" }).press("Enter");
    await expect(page.getByText(/仅显示最后 16 KiB/)).toBeVisible();
    await expect(page.getByText("Author raw log: <failure>")).toBeVisible();
    logState = 1;
    await page.getByRole("button", { name: "刷新 Worker 输出" }).press("Enter");
    await expect(page.getByText("本次执行尚未创建 Worker 日志。")).toBeVisible();
    await page.goto(`${base}/?task=${cancelled}#launch`);
    await detail.getByLabel("任务控制原因").fill("Do not admit remaining work");
    await detail.getByRole("button", { name: "取消剩余阶段" }).press("Enter");
    await expect(detail.getByText(/推进状态： 已取消/)).toBeVisible();
    await expect(detail.getByText(/已接纳的测量可能继续/)).toBeVisible();
    expect(await page.evaluate<number>("document.documentElement.scrollWidth")).toBeLessThanOrEqual(
      390,
    );
  } finally {
    if (started) stopProject(project);
    await rm(root, { recursive: true, force: true });
  }
});
