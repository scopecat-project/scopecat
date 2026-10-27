import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject, uv } from "./daemon";

const PREPARE = `
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
project.load_application()
with project.authoring() as author:
    author.refresh()
    from ui_signal.application import initial_parameters
    content = initial_parameters()
    saved = author.parameters.save(name="line-inputs", catalog=content.catalog, parameters=content.parameters)
    setup = author.setup.get("initial")
    author.use(parameters=saved, setup=setup.ref)
    prepared = author.prepare("ui_signal.signal", actor="template-operator", scans={"frequency": [sc.Quantity(v, "GHz") for v in (4.6, 4.7, 4.8, 4.9, 5.0)]})
    plan = prepared.save_plan("Synthetic S21 template", saved_by="template-operator")
    print(json.dumps({"plan": plan.ref.plan_id, "workspace": plan.definition.workspace_id}))
`;

const firstLine = "Readout A — " + "long-physical-line-name-".repeat(5);

for (const width of [1280, 390]) {
  test(`finite physical lines retain every attempt across reopening at ${width}px`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(240_000);
    page.setDefaultTimeout(15_000);
    const project = await mkdtemp(join(tmpdir(), "scopecat-line-list-"));
    let started = false;
    try {
      for (const name of ["src", "scopecat.toml"])
        await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
          recursive: true,
        });
      let base = await startProject(project);
      started = true;
      const seed = JSON.parse(uv(["python", "-c", PREPARE, project])) as {
        plan: string;
        workspace: string;
      };
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${base}/#settings`);
      await page.getByLabel("操作者名称", { exact: true }).fill("measurement-operator");
      await page.getByRole("button", { name: "保存操作者", exact: true }).click();
      await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
      await page.goto(`${base}/?workspace=${encodeURIComponent(seed.workspace)}#launch`);
      await page.getByText("物理线路扫描", { exact: true }).click();
      const region = page.getByRole("region", { name: "物理线路扫描", exact: true });
      await region.getByText("创建线路清单", { exact: true }).click();
      await region.getByLabel("测量模板").selectOption(seed.plan);
      await region.getByLabel("线路清单操作者").fill("line-operator");
      await region.getByLabel("线路清单名称").fill("Readout wiring");
      await region.getByLabel("物理线路，每行一条").fill(`${firstLine}\nReadout B`);
      await region.getByRole("button", { name: "保存线路清单", exact: true }).press("Enter");
      await expect(region.getByRole("button", { name: `准备 ${firstLine}` })).toBeEnabled();
      await expect(region.getByRole("button", { name: "准备 Readout B" })).toBeDisabled();
      const scan = new URL(page.url()).searchParams.get("line-scan")!;
      const view = async () =>
        await (await page.request.get(`${base}/api/v1/manual-line-scans/${scan}`)).json();
      const runIds: string[] = [];
      for (const [index, label] of [firstLine, "Readout B", firstLine].entries()) {
        await page.goto(
          `${base}/?workspace=${encodeURIComponent(seed.workspace)}&line-scan=${scan}#launch`,
        );
        await region
          .getByRole("button", {
            name: `${index === 2 ? "重新测量" : "准备"} ${label}`,
            exact: true,
          })
          .click();
        await expect(page.getByText(`物理线路：${label}`, { exact: false })).toBeVisible();
        await page.locator('input[aria-label="操作人"]').fill("measurement-operator");
        if (index > 0) await page.getByLabel("Amplitude", { exact: true }).fill("0.08");
        const submitted = page.waitForResponse((response) =>
          response.url().endsWith("/experiment-launcher/submit"),
        );
        await page.getByRole("button", { name: "开始采集", exact: true }).click();
        const receipt = await (await submitted).json();
        expect(receipt.procedure_id).toBeTruthy();
        await expect
          .poll(
            async () =>
              (
                await (
                  await page.request.get(`${base}/api/v1/procedures/${receipt.procedure_id}`)
                ).json()
              ).state,
            { timeout: 45_000 },
          )
          .toBe("waiting_for_input");
        expect((await (await page.request.get(`${base}/api/v1/runs`)).json()).items).toHaveLength(
          index,
        );
        if (index === 1) {
          // Reopening a pending line must retain the first curve and cannot acquire the next.
          stopProject(project);
          started = false;
          base = await startProject(project);
          started = true;
          await page.goto(
            `${base}/?workspace=${encodeURIComponent(seed.workspace)}&line-scan=${scan}#launch`,
          );
          await expect(region.getByText("已完成", { exact: true })).toBeVisible();
          await expect(
            region.getByRole("button", { name: `重新测量 ${firstLine}` }),
          ).toBeDisabled();
          expect((await (await page.request.get(`${base}/api/v1/runs`)).json()).items).toHaveLength(
            1,
          );
        }
        await page.goto(`${base}/?procedure=${receipt.procedure_id}#decisions`);
        await page.getByLabel("判断操作人", { exact: true }).fill(`wire-operator-${index}`);
        await page.getByLabel("confirmed", { exact: true }).selectOption("true");
        await page.getByRole("button", { name: "提交判断并继续", exact: true }).click();
        await expect
          .poll(
            async () =>
              (
                await (
                  await page.request.get(`${base}/api/v1/procedures/${receipt.procedure_id}`)
                ).json()
              ).closure?.status,
            { timeout: 45_000 },
          )
          .toBe("succeeded");
        const current = await view();
        const attempt = current.lines[index === 1 ? 1 : 0].attempts[index === 2 ? 1 : 0];
        runIds.push(attempt.run.snapshot.run_id);
        expect(attempt.selection.label).toBe(label);
        expect(attempt.run.control.admission.manual_line_confirmation.actor).toBe(
          `wire-operator-${index}`,
        );
      }
      stopProject(project);
      started = false;
      base = await startProject(project);
      started = true;
      await page.goto(
        `${base}/?workspace=${encodeURIComponent(seed.workspace)}&line-scan=${scan}#launch`,
      );
      await expect(region.getByText("已完成", { exact: true })).toHaveCount(2);
      await expect(region.getByRole("link", { name: "曲线与结果" })).toHaveCount(3);
      const retained = await view();
      expect(retained.lines.map((line: { attempts: unknown[] }) => line.attempts.length)).toEqual([
        2, 1,
      ]);
      expect(new Set(runIds).size).toBe(3);
      await region.getByLabel("比较此次尝试").nth(0).check();
      await region.getByLabel("比较此次尝试").nth(1).check();
      await expect(region.getByRole("region", { name: "线路测量尝试比较" })).toContainText(
        "amplitude",
      );
      await expect(region.getByRole("link", { name: "比较已保留曲线" })).toHaveAttribute(
        "href",
        new RegExp(runIds[0]),
      );
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await page.screenshot({
        path: testInfo.outputPath(`line-list-${width}.png`),
        fullPage: true,
      });
      await region.getByRole("link", { name: "比较已保留曲线" }).click();
      await expect(page.getByLabel("主 run", { exact: true })).toHaveValue(runIds[0]);
      await expect(page.getByLabel("对照 run", { exact: true })).toHaveValue(runIds[2]);
      const comparison = page.waitForResponse(
        (response) =>
          response.url().endsWith("/run-comparison") &&
          response.request().method() === "POST" &&
          response.request().postDataJSON().action === "inspect",
      );
      await page.getByRole("button", { name: "检查兼容数据", exact: true }).click();
      expect((await comparison).status()).toBe(200);
      await expect(
        page.getByRole("region", { name: "已有 run 比较" }).locator("svg"),
      ).toBeVisible();
    } finally {
      if (started) stopProject(project);
      await rm(project, { recursive: true, force: true });
    }
  });
}
