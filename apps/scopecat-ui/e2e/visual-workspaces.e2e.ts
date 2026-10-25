import { cp, mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { expect, test, type Page } from "@playwright/test";
import { ROOT, startProject, stopProject, uv } from "./daemon";

const SEED = `
import json, sys
import pyarrow as pa
import scopecat as sc
from scopecat.records.sample import SampleRevisionDraft
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
with project.connect() as lab, project.authoring() as author:
    lab.samples.create("visual-chip", kind="synthetic", content=SampleRevisionDraft(display_name="Visual sample"))
    content = initial_parameters()
    revision = author.parameters.save(name="visual-inputs", catalog=content.catalog, parameters=content.parameters)
    author.parameters.create_branch("visual-inputs", revision=revision)
    author.use(parameter_branch="visual-inputs", setup=author.setup.get("initial"), sample="visual-chip")
    receipt = author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}).run().wait().result()
    run = lab.get_run(receipt.id)
    analysis = run.analysis("Visual signal", key="visual")
    analysis.measurements(id="signal")
    saved = (analysis.result().dataset("curve", pa.table({"frequency": [4.7, 4.8, 4.9], "response": [0.1, 1.0, 0.2]}))
        .figure(dataset="curve", kind="line", x="frequency", y="response").save())
from dataclasses import dataclass
from typing import Literal
from pathlib import Path
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.automation import InterpretationRequest, ProcedureDefinitionRef, ProcedureSubmitCommand, ProcedureWorkerLeaseAcquireCommand, ProcedureStepBeginCommand, ProcedureStepInputWaitCommand
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import resolve_daemon_endpoint
@dataclass
class Answer:
    outcome: Literal["Peak selected", "No clear peak", "Unsure"]
    frequency: float | None
with DaemonClient(resolve_daemon_endpoint(Path(sys.argv[1]))) as client:
    task = client.submit_procedure(ProcedureSubmitCommand(request_key="visual-review", definition=ProcedureDefinitionRef(id="Signal review",version="1",fingerprint="sha256:"+"1"*64), intent={})).run
    lease = client.acquire_procedure_worker_lease(ProcedureWorkerLeaseAcquireCommand(procedure_run_id=task.procedure_run_id, worker_id="seed", expected_run_revision=task.revision))
    schema = AnalysisFactSchema("visual.review.v1", Answer)
    request = InterpretationRequest(title="Choose the signal peak", instructions="Inspect the curve and record a peak, or choose Unsure / No clear peak.", schema_id=schema.id, schema_hash=schema.schema_hash, structure=schema.structure, response_template={"outcome":"Unsure", "frequency":None}, metadata={"reviewer":"Visual operator", "field_labels":{"frequency":"Frequency (GHz)"}, "field_constraints":{"frequency":{"required_when":{"field":"outcome", "value":"Peak selected"},"empty_otherwise":True}}})
    begin = client.begin_procedure_step(ProcedureStepBeginCommand(procedure_run_id=task.procedure_run_id,lease_token=lease.lease.lease_token,expected_run_revision=lease.run.revision,step_key="choose",operation="interpretation",intent_hash=request.request_hash,inputs=({"kind":"analysis", "subject":{"kind":"run", "run_id":run.id}, "analysis_record_id":saved.id},)))
    client.wait_procedure_step_input(ProcedureStepInputWaitCommand(procedure_run_id=task.procedure_run_id,lease_token=lease.lease.lease_token,expected_run_revision=begin.run.revision,step_key=begin.step.step_key,attempt=begin.step.attempt,expected_step_revision=begin.step.revision,request=request))
    print(json.dumps({"run":run.id,"analysis":saved.id,"procedure":task.procedure_run_id}))
`;

test("populated workspaces, exact result window and focus view remain readable", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(180_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-visual-"));
  let started = false;
  const capture = async (target: Page, name: string) => {
    await target.evaluate("window.scrollTo(0, 0)");
    expect(
      await target.evaluate<number>("document.documentElement.scrollWidth"),
    ).toBeLessThanOrEqual(target.viewportSize()!.width);
    await testInfo.attach(name, {
      body: await target.screenshot({
        fullPage: true,
        ...(process.env.SCOPECAT_SCREENSHOTS
          ? { path: `${process.env.SCOPECAT_SCREENSHOTS}/${name}.png` }
          : {}),
      }),
      contentType: "image/png",
    });
  };
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const base = await startProject(project);
    started = true;
    const seed = JSON.parse(uv(["python", "-c", SEED, project])) as {
      run: string;
      analysis: string;
      procedure: string;
    };
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${base}/?run=${seed.run}#runs`);
      await expect(page.getByTestId("run-detail-header")).toBeVisible();
      await expect(page.getByRole("region", { name: "run 进度" })).toBeVisible();
      await capture(page, `run-${width}`);
      await page.getByRole("button", { name: "聚焦曲线", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await capture(page, `focused-chart-${width}`);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("button", { name: "聚焦曲线", exact: true })).toBeFocused();
      await page.goto(`${base}/#configuration`);
      await page.getByLabel("Working parameter branch", { exact: true }).fill("visual-inputs");
      await page.getByRole("button", { name: "Open working table", exact: true }).click();
      await expect(page.getByText("Draft operator", { exact: true })).toBeVisible();
      await capture(page, `working-parameters-${width}`);
      await page.goto(`${base}/?sample=visual-chip#samples`);
      await expect(page.getByRole("heading", { name: "Visual sample", exact: true })).toBeVisible();
      await capture(page, `sample-${width}`);
      await page.goto(`${base}/?procedure=${seed.procedure}#decisions`);
      await expect(
        page.getByRole("heading", { name: "Choose the signal peak", exact: true }),
      ).toBeVisible();
      await expect(page.getByTestId("analysis-figure").locator("svg")).toBeVisible();
      await page.getByLabel("outcome", { exact: true }).selectOption(JSON.stringify("Unsure"));
      await expect(page.getByLabel("Frequency (GHz)", { exact: true })).toHaveCount(0);
      await capture(page, `decision-curve-unsure-${width}`);
      await page
        .getByLabel("outcome", { exact: true })
        .selectOption(JSON.stringify("Peak selected"));
      await page.getByLabel("Frequency (GHz)", { exact: true }).fill("4.8");
      await capture(page, `decision-curve-selected-${width}`);
    }
    await page.goto(`${base}/?run=${seed.run}#runs`);
    const popupPromise = context.waitForEvent("page");
    await page.getByRole("link", { name: "在新标签页或窗口打开结果", exact: true }).click();
    const result = await popupPromise;
    await result.setViewportSize({ width: 768, height: 900 });
    await expect(result.getByTestId("run-detail-header")).toBeVisible();
    await page.goto(`${base}/#configuration`);
    expect(new URL(result.url()).searchParams.get("run")).toBe(seed.run);
    await capture(result, "independent-result-768");
    await result.close();
    // Render the existing reconciliation dialog against an explicitly synthetic read view.
    // Cancel only: the completed fixture run is never changed by this inspection.
    await page.route(`**/api/v1/runs/${seed.run}`, async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      data.control.state = "attention_required";
      await route.fulfill({ response, json: data });
    });
    await page.goto(`${base}/?run=${seed.run}#runs`);
    await page.getByRole("button", { name: "Close without resuming", exact: true }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await capture(page, "reconciliation-dialog-390");
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Cancel", exact: true })
      .click();
    await expect(page.getByRole("alertdialog")).toBeHidden();
  } finally {
    if (started) stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
