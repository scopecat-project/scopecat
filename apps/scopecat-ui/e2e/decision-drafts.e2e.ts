import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer } from "node:net";
import { expect, test } from "@playwright/test";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 30000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
  return result.stdout.trim();
}
const SEED = `from dataclasses import dataclass
import sys
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.automation import (InterpretationRequest, ProcedureDefinitionRef, ProcedureSubmitCommand, ProcedureWorkerLeaseAcquireCommand, ProcedureStepBeginCommand, ProcedureStepInputWaitCommand)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from pathlib import Path
@dataclass
class Answer:
    frequency: float
    rationale: str
with DaemonClient(resolve_daemon_endpoint(Path(sys.argv[1]))) as client:
    run = client.submit_procedure(ProcedureSubmitCommand(request_key='draft-test', definition=ProcedureDefinitionRef(id='draft-review',version='1',fingerprint='sha256:'+'1'*64), intent={})).run
    lease = client.acquire_procedure_worker_lease(ProcedureWorkerLeaseAcquireCommand(procedure_run_id=run.procedure_run_id, worker_id='seed', expected_run_revision=run.revision))
    schema = AnalysisFactSchema('draft.review.v1', Answer)
    request = InterpretationRequest(title='Review draft recovery', instructions='Inspect before recording', schema_id=schema.id, schema_hash=schema.schema_hash, structure=schema.structure, response_template={'frequency':6.0,'rationale':''})
    begin = client.begin_procedure_step(ProcedureStepBeginCommand(procedure_run_id=run.procedure_run_id,lease_token=lease.lease.lease_token,expected_run_revision=lease.run.revision,step_key='select/peak',operation='interpretation',intent_hash=request.request_hash))
    client.wait_procedure_step_input(ProcedureStepInputWaitCommand(procedure_run_id=run.procedure_run_id,lease_token=lease.lease.lease_token,expected_run_revision=begin.run.revision,step_key=begin.step.step_key,attempt=begin.step.attempt,expected_step_revision=begin.step.revision,request=request))
    print(run.procedure_run_id)
`;

test("Decision drafts survive two pages, navigation, changed port and failed submission", async ({
  browser,
}, testInfo) => {
  test.setTimeout(120000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-drafts-e2e-"));
  const otherHome = await mkdtemp(join(tmpdir(), "scopecat-other-drafts-e2e-"));
  const blocker = createServer();
  const endpoint = async () =>
    JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")).base_url as string;
  try {
    await writeFile(join(project, "scopecat.toml"), "[lab]\n");
    await writeFile(join(otherHome, "scopecat.toml"), "[lab]\n");
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const firstUrl = await endpoint();
    const procedure = uv(["python", "-c", SEED, project]);
    const context = await browser.newContext();
    const a = await context.newPage();
    const b = await context.newPage();
    const decisionUrl = `${firstUrl}/?procedure=${procedure}#decisions`;
    await a.goto(decisionUrl);
    await b.goto(decisionUrl);
    await expect(a.getByLabel("frequency", { exact: true })).toBeVisible();
    await expect(b.getByLabel("frequency", { exact: true })).toBeVisible();
    await a.getByLabel("Recorded reviewer").fill("operator A");
    await a.getByLabel("frequency", { exact: true }).fill("1e");
    await a.getByLabel("rationale", { exact: true }).fill("unfinished useful reasoning");
    await expect(a.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    await b.getByLabel("Reasoning note (optional)").fill("window B conflicting note");
    await expect(b.getByText(/Your conflicting copy is retained/)).toBeVisible();
    await expect(b.getByRole("button", { name: "Record decision", exact: true })).toBeDisabled();
    await expect(a.getByLabel("frequency", { exact: true })).toHaveValue("1e");
    await b.getByRole("button", { name: "Recover saved Decision drafts" }).click();
    await expect(b.getByText(/conflict · revision/)).toBeVisible();
    await a.getByRole("button", { name: "Record decision", exact: true }).click();
    await expect(a.getByRole("alert")).toContainText("Complete frequency");
    await a.goto(`${firstUrl}/#runs`);
    await a.goto(decisionUrl);
    await expect(a.getByLabel("frequency", { exact: true })).toHaveValue("1e");
    await context.close();
    uv(["scopecat", "stop", project]);
    // Occupy the prior port to make origin change deterministic.
    await new Promise<void>((done, reject) => {
      blocker.once("error", reject);
      blocker.listen(Number(new URL(firstUrl).port), "127.0.0.1", done);
    });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const secondUrl = await endpoint();
    expect(secondUrl).not.toBe(firstUrl);
    const fresh = await browser.newContext();
    const reopened = await fresh.newPage();
    await reopened.goto(`${secondUrl}/?procedure=${procedure}#decisions`);
    await expect(reopened.getByLabel("frequency", { exact: true })).toHaveValue("1e");
    await expect(reopened.getByLabel("rationale", { exact: true })).toHaveValue(
      "unfinished useful reasoning",
    );
    await reopened.getByLabel("frequency", { exact: true }).fill("6.2");
    await expect(
      reopened.getByText("Draft saved in application data", { exact: true }),
    ).toBeVisible();
    await reopened.route(
      "**/attempts/1/input",
      (route) =>
        route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "test submit failure" }),
        }),
      { times: 1 },
    );
    await reopened.getByRole("button", { name: "Record decision", exact: true }).click();
    await expect(reopened.getByRole("alert")).toContainText("test submit failure");
    await reopened.reload();
    await expect(reopened.getByLabel("frequency", { exact: true })).toHaveValue("6.2");
    await reopened.getByRole("button", { name: "Record decision", exact: true }).click();
    await expect(reopened.getByText("Decision saved or no longer waiting")).toBeVisible();
    await reopened.getByRole("button", { name: "Recover saved Decision drafts" }).click();
    await expect(reopened.getByText(/history cannot submit a decision/)).toBeVisible();
    const runs = await reopened.request.get(`${secondUrl}/api/v1/runs`);
    expect((await runs.json()).items).toHaveLength(0);
    const state = await reopened.request.get(`${secondUrl}/api/v1/procedures/${procedure}`);
    expect((await state.json()).state).toBe("ready");
    await reopened.screenshot({
      path: testInfo.outputPath("retained-completed-drafts.png"),
      fullPage: true,
    });
    uv(["scopecat", "start", otherHome, "--port", "0", "--static-dir", resolve("dist")]);
    const otherUrl = JSON.parse(
      await readFile(join(otherHome, ".scopecat/daemon.json"), "utf8"),
    ).base_url;
    const other = await reopened.request.get(`${otherUrl}/api/v1/decision-drafts`);
    expect((await other.json()).items).toHaveLength(0);
    await fresh.close();
  } finally {
    blocker.close();
    uv(["scopecat", "stop", project]);
    uv(["scopecat", "stop", otherHome]);
    await rm(project, { recursive: true, force: true });
    await rm(otherHome, { recursive: true, force: true });
  }
});
