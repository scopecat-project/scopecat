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
      await page.goto(`${base}/?workspace=${encodeURIComponent(seed.workspace)}#launch`);
      await page.getByText("Physical line scans", { exact: true }).click();
      const region = page.getByRole("region", { name: "Physical line scans", exact: true });
      await region.getByText("Create a line list", { exact: true }).click();
      await region.getByLabel("Measurement template").selectOption(seed.plan);
      await region.getByLabel("Line list operator").fill("line-operator");
      await region.getByLabel("Line list name").fill("Readout wiring");
      await region.getByLabel("Physical lines, one per line").fill("Readout A\nReadout B");
      await region.getByRole("button", { name: "Save line list", exact: true }).click();
      await expect(region.getByRole("button", { name: "Prepare Readout A" })).toBeEnabled();
      await expect(region.getByRole("button", { name: "Prepare Readout B" })).toBeDisabled();
      const scan = new URL(page.url()).searchParams.get("line-scan")!;
      const view = async () =>
        await (await page.request.get(`${base}/api/v1/manual-line-scans/${scan}`)).json();
      const runIds: string[] = [];
      for (const [index, label] of ["Readout A", "Readout B", "Readout A"].entries()) {
        await page.goto(
          `${base}/?workspace=${encodeURIComponent(seed.workspace)}&line-scan=${scan}#launch`,
        );
        await region
          .getByRole("button", {
            name: `${index === 2 ? "Remeasure" : "Prepare"} ${label}`,
            exact: true,
          })
          .click();
        await expect(page.getByText(`Physical line: ${label}`, { exact: false })).toBeVisible();
        await page.locator('input[aria-label="Operator"]').fill("measurement-operator");
        if (index > 0) await page.getByLabel("Amplitude", { exact: true }).fill("0.08");
        const submitted = page.waitForResponse((response) =>
          response.url().endsWith("/experiment-launcher/submit"),
        );
        await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
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
          await expect(region.getByText("completed", { exact: true })).toBeVisible();
          await expect(region.getByRole("button", { name: "Remeasure Readout A" })).toBeDisabled();
          expect((await (await page.request.get(`${base}/api/v1/runs`)).json()).items).toHaveLength(
            1,
          );
        }
        await page.goto(`${base}/?procedure=${receipt.procedure_id}#decisions`);
        await page.getByLabel("Recorded reviewer", { exact: true }).fill(`wire-operator-${index}`);
        await page.getByRole("checkbox").check();
        await page.getByRole("button", { name: "Submit and continue", exact: true }).click();
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
      await expect(region.getByText("completed", { exact: true })).toHaveCount(2);
      await expect(region.getByRole("link", { name: "Curve and result" })).toHaveCount(3);
      const retained = await view();
      expect(retained.lines.map((line: { attempts: unknown[] }) => line.attempts.length)).toEqual([
        2, 1,
      ]);
      expect(new Set(runIds).size).toBe(3);
      await region.getByLabel("Compare this attempt").nth(0).check();
      await region.getByLabel("Compare this attempt").nth(1).check();
      await expect(region.getByRole("region", { name: "Line attempt comparison" })).toContainText(
        "amplitude",
      );
      await expect(region.getByRole("link", { name: "Compare retained curves" })).toHaveAttribute(
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
      await region.getByRole("link", { name: "Compare retained curves" }).click();
      await expect(page.getByLabel("Primary run", { exact: true })).toHaveValue(runIds[0]);
      await expect(page.getByLabel("Secondary run", { exact: true })).toHaveValue(runIds[2]);
      const comparison = page.waitForResponse(
        (response) =>
          response.url().endsWith("/run-comparison") && response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Inspect compatible data", exact: true }).click();
      expect((await comparison).status()).toBe(200);
      await expect(
        page.getByRole("region", { name: "Retained run comparison" }).locator("svg"),
      ).toBeVisible();
    } finally {
      if (started) stopProject(project);
      await rm(project, { recursive: true, force: true });
    }
  });
}
