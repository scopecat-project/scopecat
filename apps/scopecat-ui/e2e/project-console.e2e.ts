import { spawn, spawnSync, type ChildProcess, type SpawnSyncReturns } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test as base, type Page } from "@playwright/test";

interface DaemonEndpointRecord {
  base_url: string;
}

interface ProjectDaemon {
  baseUrl: string;
  projectRoot: string;
}

interface ConfigRegistryPage {
  activation: {
    entry_id: string;
    generation: number;
  };
}

interface ConfigActivationPage {
  items: unknown[];
}

interface ProcessCompletion {
  code: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
}

interface RunningExperiment {
  child: ChildProcess;
  completion: Promise<ProcessCompletion>;
}

interface ControlledExperiment extends RunningExperiment {
  acceptedReady: string;
  releaseAccepted: string;
  runningReady: string;
  releaseRunning: string;
  measurementReady: string;
  releaseMeasurement: string;
}

interface AdaptiveExperiment extends RunningExperiment {
  runIdReady: string;
  optimizerReady: string;
  releaseOptimizer: string;
  queueAcceptedReady: string;
  releaseFinish: string;
}

interface CandidateAnalysis {
  runId: string;
  proposalId: string;
  analysisId: string;
}

const UI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPOSITORY_ROOT = resolve(UI_ROOT, "../..");
const UI_DIST = resolve(UI_ROOT, "dist");
const LIVE_DISPLAY_NAME = "Live state browser E2E";
const LIVE_EXPERIMENT_ID = "ui_e2e_live_scan";
const CONTROLLED_EXPERIMENT_SOURCE = `\
"""Exercise durable run states while a browser observes the daemon."""

from __future__ import annotations

import time
from pathlib import Path

import scopecat as sc

PROJECT_ROOT = Path(__file__).resolve().parent
CONTROL_ROOT = PROJECT_ROOT / ".scopecat"
ACCEPTED_READY = CONTROL_ROOT / "e2e-accepted-ready"
RELEASE_ACCEPTED = CONTROL_ROOT / "e2e-release-accepted"
RUNNING_READY = CONTROL_ROOT / "e2e-running-ready"
RELEASE_RUNNING = CONTROL_ROOT / "e2e-release-running"
MEASUREMENT_READY = CONTROL_ROOT / "e2e-measurement-ready"
RELEASE_MEASUREMENT = CONTROL_ROOT / "e2e-release-measurement"


def wait_for_release(path: Path) -> None:
    deadline = time.monotonic() + 45
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for {path.name}")
        time.sleep(0.02)


@sc.experiment(id="ui_e2e_live_scan")
def live_scan(experiment: sc.ExperimentContext) -> sc.CoordinateRef[int]:
    return experiment.scan("value", tuple(range(15)))


project = sc.open_project(PROJECT_ROOT)
with project.connect() as lab:
    client = lab._client
    original_submit = client.submit_run
    original_start = client.start_executor
    original_ingest = client.ingest_measurements
    prepared = lab.prepare(
        live_scan(),
    )

    def gated_submit(submission):
        admission = original_submit(submission)
        ACCEPTED_READY.write_text(admission.run_id, encoding="utf-8")
        wait_for_release(RELEASE_ACCEPTED)
        return admission

    def gated_start(run_id, request):
        lease = original_start(run_id, request)
        RUNNING_READY.write_text(run_id, encoding="utf-8")
        wait_for_release(RELEASE_RUNNING)
        return lease

    ingest_count = [0]

    def gated_ingest(run_id, *, lease_id, append, dataset_schema):
        receipt = original_ingest(
            run_id,
            lease_id=lease_id,
            append=append,
            dataset_schema=dataset_schema,
        )
        ingest_count[0] += 1
        if ingest_count[0] == 1:
            MEASUREMENT_READY.write_text(run_id, encoding="utf-8")
            wait_for_release(RELEASE_MEASUREMENT)
        return receipt

    client.submit_run = gated_submit
    client.start_executor = gated_start
    client.ingest_measurements = gated_ingest
    try:
        run = prepared.run(name="Live state browser E2E")
    finally:
        client.submit_run = original_submit
        client.start_executor = original_start
        client.ingest_measurements = original_ingest
    summary = {"run_id": run.id, "status": run.status}

print(summary)
`;

const ADAPTIVE_EXPERIMENT_SOURCE = `\
"""Pause an adaptive run so the browser can queue a free physical point."""

from __future__ import annotations

import time
from pathlib import Path

import scopecat as sc

PROJECT_ROOT = Path(__file__).resolve().parent
CONTROL_ROOT = PROJECT_ROOT / ".scopecat"
RUN_ID_READY = CONTROL_ROOT / "e2e-adaptive-run-id"
OPTIMIZER_READY = CONTROL_ROOT / "e2e-adaptive-optimizer-ready"
RELEASE_OPTIMIZER = CONTROL_ROOT / "e2e-adaptive-release-optimizer"
QUEUE_ACCEPTED_READY = CONTROL_ROOT / "e2e-adaptive-queue-accepted"
RELEASE_FINISH = CONTROL_ROOT / "e2e-adaptive-release-finish"


def wait_for_release(path: Path) -> None:
    deadline = time.monotonic() + 45
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for {path.name}")
        time.sleep(0.02)


class GatedOptimizer:
    id = "ui-e2e.gated-optimizer"

    def propose(self, context: sc.DomainOptimizerContext):
        region = context.region
        assert region is not None
        if region.completed_point_count == 2:
            OPTIMIZER_READY.write_text("ready", encoding="utf-8")
            wait_for_release(RELEASE_OPTIMIZER)
            return sc.DomainProposalAttempt(
                sc.ResolvedDomainFragment.points(({"x": 0.5},)),
                based_on_region_revisions={region.id: region.revision},
            )
        if region.completed_point_count == 4:
            QUEUE_ACCEPTED_READY.write_text("ready", encoding="utf-8")
            wait_for_release(RELEASE_FINISH)
        return sc.OptimizationComplete("browser queue exercised")


@sc.experiment(id="ui_e2e_adaptive_scan")
def adaptive_scan(experiment: sc.ExperimentContext) -> sc.CoordinateRef[float]:
    return experiment.scan("x", (0.0, 1.0))


project = sc.open_project(PROJECT_ROOT)
with project.connect() as lab:
    client = lab._client
    original_start = client.start_executor

    def observed_start(run_id, request):
        lease = original_start(run_id, request)
        RUN_ID_READY.write_text(run_id, encoding="utf-8")
        return lease

    client.start_executor = observed_start
    try:
        prepared = lab.prepare(adaptive_scan().adaptive(GatedOptimizer(), max_points=5))
        run = prepared.run(name="Adaptive operator queue E2E")
        summary = {"run_id": run.id, "status": run.status}
    finally:
        client.start_executor = original_start

print(summary)
`;

const CANDIDATE_ANALYSIS_SOURCE = `\
"""Create one durable notebook analysis for GUI acceptance."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pyarrow as pa
import scopecat as sc

project_root = Path(sys.argv[1])
with sc.open_project(project_root).connect() as lab:
    baseline = lab.runs().items[0]
    analysis = (
        baseline.analysis("notebook repetition fit").result()
        .dataset(
            "fit",
            pa.table({
                "repetitions": [128, 256, 384],
                "score": [0.73, 0.89, 0.98],
            }),
            fields={
                "repetitions": sc.AnalysisField(label="Repetitions"),
                "score": sc.AnalysisField(label="Fit score", unit="ratio"),
            },
        )
        .table(
            dataset="fit",
            title="Candidate fit",
        )
        .figure(
            dataset="fit",
            kind="line",
            x="repetitions",
            y="score",
            title="Candidate fit curve",
        )
        .propose(
            "repetitions-fit",
            sc.replace_scalar_parameter("repetitions", 384),
            reason="stable high-confidence notebook fit",
            confidence=0.98,
        )
    )
    saved = analysis.save()
    saved.candidate_config()

print(
    "E2E_CANDIDATE="
    + json.dumps(
        {
            "run_id": baseline.id,
            "proposal_id": analysis.parameter_proposals[0].id,
            "analysis_id": saved.id,
        }
    )
)
`;

const test = base.extend<{}, { daemon: ProjectDaemon }>({
  daemon: [
    async ({}, use) => {
      const projectRoot = await mkdtemp(join(tmpdir(), "scopecat-ui-e2e-"));
      let initialized = false;
      try {
        runUv(["scopecat", "init", projectRoot], REPOSITORY_ROOT);
        initialized = true;
        runUv(
          ["scopecat", "start", projectRoot, "--port", "0", "--static-dir", UI_DIST],
          projectRoot,
        );
        const endpoint = await readEndpoint(projectRoot);
        const firstRun = runUv(
          ["python", join(projectRoot, "notebooks/01_first_run.py")],
          projectRoot,
        );
        expect(firstRun.stdout).toContain("'status': 'completed'");

        await use({
          baseUrl: endpoint.base_url,
          projectRoot,
        });
      } finally {
        if (initialized) {
          await stopAndRemoveProject(projectRoot);
        } else {
          await rm(projectRoot, { recursive: true, force: true });
        }
      }
    },
    { scope: "worker", timeout: 120_000 },
  ],
});

test("starter project closes the notebook, run, and config loop", async ({ daemon, page }) => {
  await page.goto(daemon.baseUrl);

  await expect(
    page.getByRole("heading", {
      name: "First run",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText("first_run", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Succeeded", { exact: true }).first()).toBeVisible();
  const dataCard = page.getByTestId("data-card");
  await expect(dataCard.getByText(/^1 records/)).toBeVisible();
  await dataCard.getByText("Raw records", { exact: true }).click();
  await expect(dataCard.getByTestId("measurement-preview")).toContainText('"temperature"');
  await expect(dataCard.getByTestId("measurement-preview")).toContainText('"value": 0.02');
  const selectedRun = new URL(page.url()).searchParams.get("run");
  expect(selectedRun).toBeTruthy();
  await page.goto(`${daemon.baseUrl}/?run=${selectedRun}`);
  await expect(page.getByRole("heading", { name: "First run", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Configuration" }).click();
  await expect(page.getByRole("heading", { name: "Default configuration" })).toBeVisible();

  const initialRegistry = await readRegistry(page, daemon.baseUrl);
  const initialHistory = await readActivationHistory(page, daemon.baseUrl);
  const initialEntryId = initialRegistry.activation.entry_id;
  await expect(page.getByTestId("active-config-entry")).toHaveText(initialEntryId);
  await expect(page.getByRole("button", { name: "Undo" })).toBeDisabled();

  await page.getByRole("button", { name: "Edit parameters" }).click();
  const repetitions = page.getByRole("spinbutton", { name: "repetitions" });
  await expect(repetitions).toHaveValue("128");
  await repetitions.fill("256");

  const setDefaultResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/config-registry/publish-operations"),
  );
  await page.getByRole("button", { name: "Set as default" }).click();
  await expectResponseOk(await setDefaultResponse, "POST");

  await expect(page.getByText("Runtime-derived default")).toBeVisible();
  await expect(page.getByTestId("parameter-atom")).toHaveText("256");
  const editedRegistry = await readRegistry(page, daemon.baseUrl);
  const editedHistory = await readActivationHistory(page, daemon.baseUrl);
  expect(editedRegistry.activation.entry_id).not.toBe(initialEntryId);
  expect(editedRegistry.activation.generation).toBe(initialRegistry.activation.generation + 1);
  expect(editedHistory.items).toHaveLength(initialHistory.items.length + 1);

  const undoResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/config-registry/activation-operations"),
  );
  await page.getByRole("button", { name: "Undo" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Restore default", exact: true })
    .click();
  await expectResponseOk(await undoResponse, "POST");

  await expect(page.getByTestId("active-config-entry")).toHaveText(initialEntryId);
  await expect(page.getByText("Runtime-derived default")).toHaveCount(0);
  const rolledBackRegistry = await readRegistry(page, daemon.baseUrl);
  const rolledBackHistory = await readActivationHistory(page, daemon.baseUrl);
  expect(rolledBackRegistry.activation.entry_id).toBe(initialEntryId);
  expect(rolledBackRegistry.activation.generation).toBe(editedRegistry.activation.generation + 1);
  expect(rolledBackHistory.items).toHaveLength(editedHistory.items.length + 1);
});

test("accepts a notebook candidate in the GUI and preserves its provenance", async ({
  daemon,
  page,
}) => {
  const candidate = await createCandidateAnalysis(daemon.projectRoot);
  const initialRegistry = await readRegistry(page, daemon.baseUrl);

  await page.goto(`${daemon.baseUrl}/?run=${encodeURIComponent(candidate.runId)}`);
  const analyses = page.getByTestId("resource-card").filter({ hasText: "Analyses" });
  await analyses.getByText("notebook repetition fit", { exact: true }).click();
  await expect(analyses.getByRole("table", { name: "Candidate fit" })).toBeVisible();
  await expect(
    analyses.getByRole("img", {
      name: "Candidate fit curve: Fit score (ratio) by Repetitions",
    }),
  ).toBeVisible();
  const proposals = page.getByTestId("run-proposals-card");
  await expect(proposals.getByText(candidate.proposalId, { exact: true })).toBeVisible();
  await expect(proposals.getByText("98% confidence", { exact: true })).toBeVisible();
  await proposals.getByPlaceholder("Evidence or rationale").fill("fit accepted from the GUI");

  const acceptResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/config-registry/publish-operations"),
  );
  await proposals.getByRole("button", { name: "Accept as default" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Accept as default" }).click();
  await expectResponseOk(await acceptResponse, "POST");
  await expect(proposals.getByRole("button", { name: "Default set" })).toBeVisible();

  const acceptedRegistry = await readRegistry(page, daemon.baseUrl);
  expect(acceptedRegistry.activation.entry_id).not.toBe(initialRegistry.activation.entry_id);
  expect(acceptedRegistry.activation.generation).toBe(initialRegistry.activation.generation + 1);

  await page.getByRole("button", { name: "Configuration" }).click();
  await expect(page.getByText("Runtime-derived default")).toBeVisible();
  await expect(page.getByText("Analysis candidate", { exact: true })).toBeVisible();
  await expect(page.getByText(candidate.analysisId, { exact: true })).toBeVisible();
  await expect(page.getByText("Approved · local-operator", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open producing run" }).click();

  await expect(page.getByRole("button", { name: "Runs" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText(candidate.runId, { exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("run")).toBe(candidate.runId);

  await page.getByRole("button", { name: "Configuration" }).click();
  const undoResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/config-registry/activation-operations"),
  );
  await page.getByRole("button", { name: "Undo" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Restore default", exact: true })
    .click();
  await expectResponseOk(await undoResponse, "POST");
  await expect(page.getByTestId("active-config-entry")).toHaveText(
    initialRegistry.activation.entry_id,
  );
});

test("open console reconnects SSE and follows a live notebook run", async ({ daemon, page }) => {
  let streamCount = 0;
  const streamRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/events/stream?")) {
      streamRequests.push(request.url());
    }
  });
  await page.route("**/api/v1/events/stream?*", async (route) => {
    streamCount += 1;
    if (streamCount === 1) {
      const url = new URL(route.request().url());
      url.searchParams.set("after", "0");
      url.searchParams.set("follow", "false");
      await route.continue({ url: url.toString() });
      return;
    }
    await route.continue();
  });
  await page.goto(daemon.baseUrl);
  // Chromium reconnects after the finite first response; transport tests cover
  // Last-Event-ID cursor precedence independently of browser routing.
  await expect.poll(() => streamRequests.length).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Ok", { exact: true }).first()).toBeVisible();

  const experiment = await startControlledExperiment(daemon.projectRoot);
  try {
    const runId = await waitForMarker(experiment.acceptedReady, experiment);
    const runItem = page.getByTestId("run-list-item").filter({ hasText: LIVE_DISPLAY_NAME });
    await expect(runItem).toContainText("Accepted");
    await expect(runItem).toContainText(LIVE_EXPERIMENT_ID);
    await runItem.click();

    const detail = page.getByRole("region", { name: "Selected run details" });
    const state = detail.getByTestId("run-status");
    const timeline = detail.getByTestId("timeline-card");
    await expect(detail.getByRole("heading", { name: LIVE_DISPLAY_NAME })).toBeVisible();
    await expect(detail.getByText(LIVE_EXPERIMENT_ID, { exact: true })).toBeVisible();
    await expect(detail.getByText(runId, { exact: true })).toBeVisible();
    await expect(state).toHaveText("Accepted");
    await expect(timeline.getByText("Run admitted", { exact: true })).toBeVisible();

    await writeFile(experiment.releaseAccepted, "", "utf8");
    await waitForMarker(experiment.runningReady, experiment);
    await expect(state).toHaveText("Running");
    await expect(timeline.getByText(/From: queued.*To: leased/)).toBeVisible();

    await writeFile(experiment.releaseRunning, "", "utf8");
    await waitForMarker(experiment.measurementReady, experiment);
    await expect(state).toHaveText("Running");
    const dataCard = detail.getByTestId("data-card");
    await expect(dataCard.getByText("Measurement data", { exact: true })).toBeVisible();
    await expect(dataCard.getByText(/^1 records/)).toBeVisible();
    await dataCard.getByText("Raw records", { exact: true }).click();
    await expect(dataCard.getByTestId("measurement-preview")).toBeVisible();
    await expect(dataCard.getByTestId("measurement-preview")).toContainText('"point_index": 0');
    // Daemon receipt makes the completed point visible before durable batching flushes it.
    await expect(
      detail.getByRole("progressbar", {
        name: "1 of 15 points complete",
      }),
    ).toBeVisible();

    await writeFile(experiment.releaseMeasurement, "", "utf8");
    const completion = await experiment.completion;
    expectProcessOk(completion);
    await expect(state).toHaveText("Succeeded");
    await expect(dataCard.getByText(/^15 records/)).toBeVisible();
    await expect(timeline.getByText(/From: leased.*To: closed/)).toBeVisible();
  } finally {
    await finishControlledExperiment(experiment);
  }
});

test("queues a free off-grid scan domain into a running adaptive compiler", async ({
  daemon,
  page,
}) => {
  const experiment = await startAdaptiveExperiment(daemon.projectRoot);
  try {
    const runId = await waitForMarker(experiment.runIdReady, experiment);
    await waitForMarker(experiment.optimizerReady, experiment);
    await page.goto(`${daemon.baseUrl}/?run=${encodeURIComponent(runId)}`);

    const detail = page.getByRole("region", { name: "Selected run details" });
    await expect(detail.getByTestId("run-status")).toHaveText("Running");
    const inspection = detail.getByTestId("run-domain-decision-card");
    await inspection.getByRole("button", { name: "Free scan" }).click();
    await inspection.getByLabel("x values").fill("0.375");
    await expect(inspection.getByText(/x \[0\.375\]/)).toBeVisible();

    const queueResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/v1/runs/${runId}/point-plan/queue`),
    );
    await inspection.getByRole("button", { name: "Add scan" }).click();
    await expectResponseOk(await queueResponse, "POST");
    await expect(inspection.getByText("pending", { exact: true })).toBeVisible();

    await writeFile(experiment.releaseOptimizer, "", "utf8");
    await waitForMarker(experiment.queueAcceptedReady, experiment);
    await expect(inspection.getByText(/accepted.*point #4/i)).toBeVisible();

    await writeFile(experiment.releaseFinish, "", "utf8");
    const completion = await experiment.completion;
    expectProcessOk(completion);
    await expect(detail.getByTestId("run-status")).toHaveText("Succeeded");
  } finally {
    await finishAdaptiveExperiment(experiment);
  }
});

async function readRegistry(page: Page, baseUrl: string): Promise<ConfigRegistryPage> {
  const response = await page.request.get(`${baseUrl}/api/v1/config-registry?limit=100`);
  await expectResponseOk(response, "GET");
  return (await response.json()) as ConfigRegistryPage;
}

async function readActivationHistory(page: Page, baseUrl: string): Promise<ConfigActivationPage> {
  const response = await page.request.get(
    `${baseUrl}/api/v1/config-registry/activations?limit=100`,
  );
  await expectResponseOk(response, "GET");
  return (await response.json()) as ConfigActivationPage;
}

async function createCandidateAnalysis(projectRoot: string): Promise<CandidateAnalysis> {
  const script = join(projectRoot, "e2e_candidate_analysis.py");
  await writeFile(script, CANDIDATE_ANALYSIS_SOURCE, "utf8");
  const result = runUv(["python", script, projectRoot], projectRoot);
  const marker = result.stdout.split(/\r?\n/).find((line) => line.startsWith("E2E_CANDIDATE="));
  if (marker === undefined) {
    throw new Error(`Candidate analysis did not report its identity:\n${result.stdout}`);
  }
  const parsed = JSON.parse(marker.slice("E2E_CANDIDATE=".length)) as {
    run_id: string;
    proposal_id: string;
    analysis_id: string;
  };
  return {
    runId: parsed.run_id,
    proposalId: parsed.proposal_id,
    analysisId: parsed.analysis_id,
  };
}

async function expectResponseOk(
  response: {
    ok(): boolean;
    status(): number;
    text(): Promise<string>;
    url(): string;
  },
  method: string,
): Promise<void> {
  if (!response.ok()) {
    throw new Error(
      `${method} ${response.url()} returned ${response.status()}: ${await response.text()}`,
    );
  }
}

async function readEndpoint(projectRoot: string): Promise<DaemonEndpointRecord> {
  const source = await readFile(join(projectRoot, ".scopecat/daemon.json"), "utf8");
  const record = JSON.parse(source) as Partial<DaemonEndpointRecord>;
  if (typeof record.base_url !== "string" || new URL(record.base_url).port === "0") {
    throw new Error(`Invalid daemon endpoint record: ${source}`);
  }
  return record as DaemonEndpointRecord;
}

function runUv(arguments_: string[], cwd: string, allowFailure = false): SpawnSyncReturns<string> {
  const environment = { ...process.env };
  delete environment.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", REPOSITORY_ROOT, ...arguments_], {
    cwd,
    encoding: "utf8",
    env: environment,
    timeout: 30_000,
  });
  if (!allowFailure && (result.error || result.status !== 0)) {
    throw new Error(
      [
        `uv ${arguments_.join(" ")} failed with status ${result.status}`,
        result.error?.message,
        result.stdout,
        result.stderr,
      ]
        .filter(Boolean)
        .join("\n"),
    );
  }
  return result;
}

async function startControlledExperiment(projectRoot: string): Promise<ControlledExperiment> {
  const script = join(projectRoot, "e2e_live_run.py");
  const acceptedReady = join(projectRoot, ".scopecat/e2e-accepted-ready");
  const releaseAccepted = join(projectRoot, ".scopecat/e2e-release-accepted");
  const runningReady = join(projectRoot, ".scopecat/e2e-running-ready");
  const releaseRunning = join(projectRoot, ".scopecat/e2e-release-running");
  const measurementReady = join(projectRoot, ".scopecat/e2e-measurement-ready");
  const releaseMeasurement = join(projectRoot, ".scopecat/e2e-release-measurement");
  await writeFile(script, CONTROLLED_EXPERIMENT_SOURCE, "utf8");

  const environment = { ...process.env };
  delete environment.SCOPECAT_DAEMON_URL;
  const child = spawn("uv", ["run", "--locked", "--project", REPOSITORY_ROOT, "python", script], {
    cwd: projectRoot,
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout?.setEncoding("utf8");
  child.stderr?.setEncoding("utf8");
  child.stdout?.on("data", (chunk: string) => {
    stdout += chunk;
  });
  child.stderr?.on("data", (chunk: string) => {
    stderr += chunk;
  });
  const completion = new Promise<ProcessCompletion>((resolveCompletion) => {
    child.once("error", (error) => {
      stderr += `\n${error.message}`;
    });
    child.once("close", (code, signal) => {
      resolveCompletion({ code, signal, stdout, stderr });
    });
  });
  return {
    acceptedReady,
    releaseAccepted,
    runningReady,
    releaseRunning,
    measurementReady,
    releaseMeasurement,
    child,
    completion,
  };
}

async function startAdaptiveExperiment(projectRoot: string): Promise<AdaptiveExperiment> {
  const script = join(projectRoot, "e2e_adaptive_run.py");
  const runIdReady = join(projectRoot, ".scopecat/e2e-adaptive-run-id");
  const optimizerReady = join(projectRoot, ".scopecat/e2e-adaptive-optimizer-ready");
  const releaseOptimizer = join(projectRoot, ".scopecat/e2e-adaptive-release-optimizer");
  const queueAcceptedReady = join(projectRoot, ".scopecat/e2e-adaptive-queue-accepted");
  const releaseFinish = join(projectRoot, ".scopecat/e2e-adaptive-release-finish");
  await writeFile(script, ADAPTIVE_EXPERIMENT_SOURCE, "utf8");

  const environment = { ...process.env };
  delete environment.SCOPECAT_DAEMON_URL;
  const child = spawn("uv", ["run", "--locked", "--project", REPOSITORY_ROOT, "python", script], {
    cwd: projectRoot,
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout?.setEncoding("utf8");
  child.stderr?.setEncoding("utf8");
  child.stdout?.on("data", (chunk: string) => {
    stdout += chunk;
  });
  child.stderr?.on("data", (chunk: string) => {
    stderr += chunk;
  });
  const completion = new Promise<ProcessCompletion>((resolveCompletion) => {
    child.once("error", (error) => {
      stderr += `\n${error.message}`;
    });
    child.once("close", (code, signal) => {
      resolveCompletion({ code, signal, stdout, stderr });
    });
  });
  return {
    runIdReady,
    optimizerReady,
    releaseOptimizer,
    queueAcceptedReady,
    releaseFinish,
    child,
    completion,
  };
}

async function waitForMarker(path: string, experiment: RunningExperiment): Promise<string> {
  await expect
    .poll(
      async () => {
        const marker = await readFile(path, "utf8").catch(() => "");
        const earlyExit = await processResultWithin(experiment, 0);
        if (earlyExit !== undefined) {
          expectProcessOk(earlyExit);
          throw new Error(`controlled experiment exited before ${path}`);
        }
        return marker.trim();
      },
      { message: `waiting for controlled experiment marker ${path}` },
    )
    .not.toBe("");
  return (await readFile(path, "utf8")).trim();
}

function expectProcessOk(completion: ProcessCompletion): void {
  expect(
    completion.code,
    [
      `controlled experiment exited with code ${completion.code}`,
      completion.signal && `signal: ${completion.signal}`,
      completion.stdout,
      completion.stderr,
    ]
      .filter(Boolean)
      .join("\n"),
  ).toBe(0);
  expect(completion.stdout).toContain("'status': 'completed'");
}

async function finishControlledExperiment(experiment: ControlledExperiment): Promise<void> {
  await Promise.all([
    writeFile(experiment.releaseAccepted, "", "utf8"),
    writeFile(experiment.releaseRunning, "", "utf8"),
    writeFile(experiment.releaseMeasurement, "", "utf8"),
  ]);
  if ((await processResultWithin(experiment, 5_000)) === undefined) {
    experiment.child.kill("SIGTERM");
    await experiment.completion;
  }
}

async function finishAdaptiveExperiment(experiment: AdaptiveExperiment): Promise<void> {
  await Promise.all([
    writeFile(experiment.releaseOptimizer, "", "utf8"),
    writeFile(experiment.releaseFinish, "", "utf8"),
  ]);
  if ((await processResultWithin(experiment, 5_000)) === undefined) {
    experiment.child.kill("SIGTERM");
    await experiment.completion;
  }
}

async function processResultWithin(
  experiment: RunningExperiment,
  timeoutMs: number,
): Promise<ProcessCompletion | undefined> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      experiment.completion,
      new Promise<undefined>((resolveTimeout) => {
        timer = setTimeout(() => resolveTimeout(undefined), timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

async function stopAndRemoveProject(projectRoot: string): Promise<void> {
  const stopped = runUv(["scopecat", "stop", projectRoot], projectRoot, true);
  if (stopped.error || stopped.status !== 0) {
    const daemonLog = await readFile(join(projectRoot, ".scopecat/daemon.log"), "utf8").catch(
      () => "",
    );
    throw new Error(
      [
        `Could not stop test daemon; project retained at ${projectRoot}`,
        stopped.error?.message,
        stopped.stdout,
        stopped.stderr,
        daemonLog && `Daemon log:\n${daemonLog}`,
      ]
        .filter(Boolean)
        .join("\n"),
    );
  }
  await rm(projectRoot, { recursive: true, force: true });
}
