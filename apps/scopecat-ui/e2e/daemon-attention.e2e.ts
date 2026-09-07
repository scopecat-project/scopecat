import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test as base, type Page } from "@playwright/test";

interface DaemonEndpointRecord {
  base_url: string;
}

interface ProjectDaemon {
  baseUrl: string;
}

interface ActiveConfigView {
  activation: {
    generation: number;
  };
  config: Record<string, unknown>;
}

interface RunAdmission {
  snapshot: {
    run_id: string;
  };
}

interface RunDetail {
  control: {
    state: string;
    attention_reason?: string | null;
  };
  snapshot: {
    outcome?: {
      problems?: Array<{ code?: string }>;
    } | null;
  };
  resources: Array<{
    resource: { id: string; kind: string };
    status: string;
  }>;
}

interface AttentionResolutionReceipt {
  state: string;
  released_resource_count: number;
}

interface EventPage {
  items: Array<{
    kind: string;
    payload: Record<string, unknown>;
  }>;
}

interface AbandonedRun {
  experimentId: string;
  resourceId: string;
  runId: string;
}

interface HttpResponse {
  json(): Promise<unknown>;
  ok(): boolean;
  status(): number;
  text(): Promise<string>;
  url(): string;
}

const UI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPOSITORY_ROOT = resolve(UI_ROOT, "../..");
const UI_DIST = resolve(UI_ROOT, "dist");

const test = base.extend<{}, { daemon: ProjectDaemon }>({
  daemon: [
    async ({}, use) => {
      const projectRoot = await mkdtemp(join(tmpdir(), "scopecat-ui-recovery-e2e-"));
      let initialized = false;
      try {
        runUv(["scopecat", "init", projectRoot], REPOSITORY_ROOT);
        initialized = true;
        runUv(
          [
            "scopecat",
            "start",
            projectRoot,
            "--port",
            "0",
            "--static-dir",
            UI_DIST,
            "--executor-lease-ttl-seconds",
            "1",
          ],
          projectRoot,
        );
        const endpoint = await readEndpoint(projectRoot);
        await use({ baseUrl: endpoint.base_url });
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

test("handles naturally expired executors from the GUI", async ({ daemon, page }) => {
  test.setTimeout(30_000);
  await page.goto(daemon.baseUrl);

  const active = await checkedJson<ActiveConfigView>(
    await page.request.get(`${daemon.baseUrl}/api/v1/config-registry/active`),
    "GET",
  );
  const run = await startAbandonedRun(page, daemon.baseUrl, active, "expired");

  await expect(page.getByTitle(`Inspect run ${run.runId}`)).toContainText("Running");
  await assertResourceStatus(page, run, "Active");

  await expect
    .poll(async () => (await getRunDetail(page, daemon.baseUrl, run.runId)).control.state, {
      message: "the abandoned executor lease should expire",
      timeout: 8_000,
      intervals: [250],
    })
    .toBe("attention_required");

  await selectAttentionRun(page, run);
  await assertResourceStatus(page, run, "Quarantined");
  await expect(page.getByRole("alert")).toContainText(
    "resume this run from Python to create a new execution segment",
  );
  await expect(page.getByRole("button", { name: "Requeue", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("execution-segments-card")).toContainText("Interrupted");
  await expect(page.getByTestId("execution-segments-card")).toContainText("0 → 0");

  const receipt = await resolveAttention(page, run.runId, "Close without resuming", "Close run");
  expect(receipt).toMatchObject({
    state: "closed",
    released_resource_count: 1,
  });
  await expect(page.getByTestId("run-status")).toHaveText("Failed");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await assertSelectedResourceStatus(page, run.resourceId, "Released");

  const resolved = await getRunDetail(page, daemon.baseUrl, run.runId);
  expect(resolved.control.state).toBe("closed");
  expect(resolved.snapshot.outcome?.problems?.[0]?.code).toBe("daemon.executor_loss_reconciled");
  expect(resolved.resources[0]?.status).toBe("released");
  await expectExpiredLeaseEvents(page, daemon.baseUrl, run.runId);
});

async function startAbandonedRun(
  page: Page,
  baseUrl: string,
  active: ActiveConfigView,
  suffix: string,
): Promise<AbandonedRun> {
  const experimentId = `scopecat.e2e.${suffix}`;
  const resourceId = `scope-${suffix}`;
  const executorId = `e2e-${suffix}`;
  const config = active.config;
  const system = config.system as Record<string, unknown>;
  const registry = system.instrument_registry as Record<string, unknown>;
  const runConfig = {
    ...config,
    system: {
      ...system,
      instrument_registry: {
        ...registry,
        instruments: [
          ...(registry.instruments as unknown[]),
          {
            id: resourceId,
            exclusivity_key: `rack-a/${resourceId}`,
            driver_id: "tests.e2e.instrument",
            connection: { kind: "virtual" },
            run_start: "preserve",
            success_action: "release",
            failure_action: "abort_and_release",
          },
        ],
      },
    },
  };
  await expectResponseOk(
    await page.request.post(`${baseUrl}/api/v1/config-registry/publish-operations`, {
      data: {
        operation_id: `e2e-config-${suffix}`,
        source: {
          kind: "direct_config_profile",
          config: runConfig,
        },
        actor: "e2e",
        expected_generation: active.activation.generation,
        entry_id: `e2e-${suffix}`,
      },
    }),
    "POST",
  );
  const admission = await checkedJson<RunAdmission>(
    await page.request.post(`${baseUrl}/api/v1/runs`, {
      data: {
        submission_id: `e2e-${suffix}`,
        config: runConfig,
        request: {},
        plan: {
          experiment_id: experimentId,
          experiment_kind: "scratch",
          point_count: 1,
          initial_point_count: 1,
          point_limit: 1,
          point_plan_fingerprint: "a".repeat(64),
          measurement_contract_fingerprint: "b".repeat(64),
          run_resource_requirements: [
            {
              id: resourceId,
              kind: "instrument",
            },
          ],
        },
      },
    }),
    "POST",
  );
  const runId = admission.snapshot.run_id;
  await expectResponseOk(
    await page.request.post(`${baseUrl}/api/v1/runs/${encodeURIComponent(runId)}/executor/start`, {
      data: {
        executor_id: executorId,
      },
    }),
    "POST",
  );
  return { experimentId, resourceId, runId };
}

async function selectAttentionRun(page: Page, run: AbandonedRun): Promise<void> {
  await page.getByTitle(`Inspect run ${run.runId}`).click();
  await expect(
    page.getByTestId("run-detail-header").getByRole("heading", {
      name: run.experimentId,
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByTestId("run-status")).toHaveText("Needs attention");
  await expect(page.getByRole("alert")).toContainText("Operator attention required");
  await expect(page.getByRole("alert")).toContainText("executor_lease_expired");
}

async function assertResourceStatus(page: Page, run: AbandonedRun, status: string): Promise<void> {
  await page.getByTitle(`Inspect run ${run.runId}`).click();
  await assertSelectedResourceStatus(page, run.resourceId, status);
}

async function assertSelectedResourceStatus(
  page: Page,
  resourceId: string,
  status: string,
): Promise<void> {
  const resource = page.getByTestId(`resource-${resourceId}`);
  await expect(resource).toContainText(status);
}

async function resolveAttention(
  page: Page,
  runId: string,
  buttonName: string,
  confirmName: string,
): Promise<AttentionResolutionReceipt> {
  const response = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "POST" &&
      new URL(candidate.url()).pathname === `/api/v1/runs/${encodeURIComponent(runId)}/attention`,
  );
  await page.getByRole("button", { name: buttonName, exact: true }).click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation).toBeVisible();
  await confirmation
    .getByRole("button", {
      name: confirmName,
      exact: true,
    })
    .click();
  return checkedJson<AttentionResolutionReceipt>(await response, "POST");
}

async function getRunDetail(page: Page, baseUrl: string, runId: string): Promise<RunDetail> {
  return checkedJson<RunDetail>(
    await page.request.get(`${baseUrl}/api/v1/runs/${encodeURIComponent(runId)}`),
    "GET",
  );
}

async function expectExpiredLeaseEvents(page: Page, baseUrl: string, runId: string): Promise<void> {
  const events = await checkedJson<EventPage>(
    await page.request.get(`${baseUrl}/api/v1/events`, {
      params: {
        limit: 100,
        latest: "true",
        run_id: runId,
      },
    }),
    "GET",
  );
  const kinds = events.items.map((event) => event.kind);
  expect(kinds).toContain("resources_quarantined");
  expect(kinds).toContain("resources_released");
  const lost = events.items.find((event) => event.kind === "executor_lease_lost");
  expect(lost?.payload.reason).toBe("executor_lease_expired");
}

async function checkedJson<T>(response: HttpResponse, method: string): Promise<T> {
  await expectResponseOk(response, method);
  return (await response.json()) as T;
}

async function expectResponseOk(response: HttpResponse, method: string): Promise<void> {
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
