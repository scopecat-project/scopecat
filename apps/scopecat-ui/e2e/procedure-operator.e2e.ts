import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import {
  chooseReferenceContext,
  prepareReferenceContexts,
  reviewRetainedExperiment,
} from "./reference-context";

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

const ADMIT = `
import sys
import scopecat as sc
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.scientific_selection import ScientificSelection, ParameterConfiguration
from scopecat.author_workspaces import author_workspace_id
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from scopecat_server.author_worker import revision_project
project = sc.open_project(sys.argv[1])
with project.authoring() as author:
    catalog = author.catalog()
assert catalog.code_revision is not None
# Seed the admission-only boundary with the same immutable composition that the
# worker restores. The public author submit API also dispatches the procedure.
application = revision_project(project.root, catalog.code_revision).load_application()
with application.connect(resolve_daemon_endpoint(project.root)) as lab:
    provider = application.launch_provider
    entry = application.authors.get("reference_lab.temperature_diagnostic").entry
    catalog_entry = next(item for item in catalog.entries if item.id == entry.id)
    assert entry == catalog_entry
    parameters = lab.parameters.checkout("browser").head.revision
    setup = lab.setup.get("browser-bench-a")
    request = LaunchRequest(workspace_id=author_workspace_id(project.root), action="preview", experiment=entry.id, version=entry.version, selection=ScientificSelection(configuration=ParameterConfiguration(ref=parameters, setup=setup.ref)))
    preview = provider(lab, request)
    admitted = provider(lab, LaunchRequest.model_validate({
        **request.model_dump(), "action": "submit", "request_key": "browser-retained",
        "expected_request_hash": preview.request_hash, "reviewed": preview.reviewed,
        "manual_state": preview.manual_state,
    }))
    retained = lab.procedures.get(admitted.procedure_id).snapshot
    assert retained.source is not None
    assert retained.source.workspace_id == catalog.workspace_id
    assert retained.source.code_revision == catalog.code_revision
    assert retained.definition.id == f"scopecat.author:{entry.id}"
    assert retained.definition.fingerprint == catalog_entry.version
    print(admitted.procedure_id)
`;

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
      const template = join(ROOT, "examples/reference_lab");
      for (const name of ["src", "config", "scopecat.toml"])
        await cp(join(template, name), join(project, name), { recursive: true });
      uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
      const endpoint = JSON.parse(
        await readFile(join(project, ".scopecat/daemon.json"), "utf8"),
      ) as {
        base_url: string;
      };
      prepareReferenceContexts(uv, project);
      const procedureId = uv(["python", "-c", ADMIT, project]);
      uv(["scopecat", "stop", project]);
      uv([
        "scopecat",
        "start",
        project,
        "--port",
        new URL(endpoint.base_url).port,
        "--static-dir",
        resolve("dist"),
      ]);
      await use({ project, baseUrl: endpoint.base_url, procedureId });
    } finally {
      uv(["scopecat", "stop", project]);
      await rm(project, { recursive: true, force: true });
    }
  },
});

retainedProcedureTest(
  "reopens an admitted procedure after restart and follows exact retained run and analysis",
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
      await expect(page.getByLabel("Experiment", { exact: true })).toBeVisible();
      await page.getByText("Retained procedures", { exact: true }).click();
      await page
        .getByRole("button", { name: /author:reference_lab.temperature_diagnostic/ })
        .click();
      await expect(page.getByText("Admitted — not dispatched", { exact: true })).toBeVisible();
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
      await page.getByRole("button", { name: "Continue task", exact: true }).click();
      const dispatch = await dispatchResponse;
      expect(dispatch.ok(), await dispatch.text()).toBe(true);
      const receipt = (await dispatch.json()) as { dispatch_error: string | null };
      expect(receipt.dispatch_error).toBeNull();
      // Observe the real child-run admission separately from its acquisition,
      // just as the source/candidate workflow below observes each durable step.
      const childRun = page.getByRole("link", { name: /^Open (current child|retained) run:/ });
      await expect(childRun).toBeVisible();
      const childHref = await childRun.getAttribute("href");
      expect(childHref).toContain(`procedure=${procedureId}`);
      await expect(page.getByRole("status").filter({ hasText: /^Completed$/ })).toBeVisible();
      await page.reload();
      await expect(page.getByRole("status").filter({ hasText: /^Completed$/ })).toBeVisible();
      const retainedRun = page.getByRole("link", { name: /^Open retained run:/ });
      await expect(retainedRun).toHaveAttribute("href", childHref!);
      await retainedRun.click();
      await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
      await expect(page.getByText("Measurement data", { exact: true })).toBeVisible();

      const runScreenshot = testInfo.outputPath("operator-retained-run.png");
      await page.screenshot({ path: runScreenshot, fullPage: true });
      await testInfo.attach("Retained run", { path: runScreenshot, contentType: "image/png" });
      await page.goto(`${endpoint.base_url}/#launch`);
      await page.getByLabel("Experiment", { exact: true }).selectOption("channel-timing");
      await chooseReferenceContext(page);
      await reviewRetainedExperiment(page);
      await expect(
        page.getByRole("button", { name: "Start acquisition", exact: true }),
      ).toBeDisabled();
      await page.getByRole("button", { name: "Preview", exact: true }).click();
      await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
      const sourceScope = page.getByRole("region", { name: "Selected-configuration source run" });
      const candidateScope = page.getByRole("region", {
        name: "Proposed-configuration verification run",
      });
      await expect(sourceScope.getByText(/^Exact: 64 shots/)).toBeVisible();
      await expect(candidateScope.getByText(/^Exact: 64 shots/)).toBeVisible();
      await expect(candidateScope.getByText(/^Unknown \(s\)/)).toBeVisible();
      await expect(candidateScope.getByText("Retained (planned dataset)")).toBeVisible();
      await expect(candidateScope.getByText(/has not run or been verified/)).toBeVisible();
      const preflightScreenshot = testInfo.outputPath("bounded-preflight.png");
      await page.screenshot({ path: preflightScreenshot, fullPage: true });
      await testInfo.attach("Bounded source and candidate preflight", {
        path: preflightScreenshot,
        contentType: "image/png",
      });
      const submissionResponse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/v1/experiment-launcher/submit" &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Start acquisition" }).click();
      // Admission launches a separate project worker. Observe that boundary before
      // budgeting the existing execution milestones, rather than timing both together.
      const response = await submissionResponse;
      expect(response.ok()).toBe(true);
      const submitted = (await response.json()) as {
        procedure_id: string;
        dispatch_error: string | null;
      };
      expect(submitted.dispatch_error).toBeNull();
      await expect(page).toHaveURL(new RegExp(`procedure=${submitted.procedure_id}`));
      // This procedure runs a source acquisition, analysis, and a second acquisition.
      // Observe each durable milestone instead of spending one UI wait on all three.
      const sourceStep = page.getByRole("listitem").filter({
        has: page.getByText(/^source: (Running|Completed)$/),
      });
      // Source and candidate share an experiment name; select the source step
      // even when it completes before the next browser observation.
      const sourceRun = page
        .getByText(/^Current step: source ·/)
        .locator("..")
        .getByRole("link", { name: /^Open current child run:/ })
        .or(sourceStep.getByRole("link", { name: /^Open retained run:/ }));
      await expect(sourceRun).toBeVisible();
      const sourceHref = await sourceRun.getAttribute("href");
      expect(sourceHref).toContain(`procedure=${submitted.procedure_id}`);
      await expect(page.getByText("source: Completed", { exact: true })).toBeVisible();
      await expect(sourceStep.getByRole("link", { name: /^Open retained run:/ })).toHaveAttribute(
        "href",
        sourceHref!,
      );
      await expect(page.getByText("candidate: Completed", { exact: true })).toBeVisible();
      await expect(
        page.getByRole("status").filter({ hasText: /^Waiting for review$/ }),
      ).toBeVisible();
      const analysisLink = page.getByRole("link", { name: "Open analysis", exact: true });
      const href = await analysisLink.getAttribute("href");
      expect(href).toContain("run-analysis=");
      await analysisLink.click();
      await expect(
        page.getByRole("heading", { name: "Channel timing candidate", exact: true }),
      ).toBeVisible();
      expect(new URL(page.url()).searchParams.get("procedure")).toBeTruthy();
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
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
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
    await page
      .getByLabel("Experiment", { exact: true })
      .selectOption("reference_lab.frequency_amplitude");
    await chooseReferenceContext(page);
    await page.getByLabel("Sample ID").fill("sample-navigation");
    await page.getByRole("textbox", { name: "Operator", exact: true }).fill("draft-author");
    await page.getByLabel("Frequency source").selectOption("range");
    await page.getByLabel("Frequency unit").selectOption("MHz");
    await page.getByLabel("Frequency start").fill("4700");
    await page.getByLabel("Frequency stop").fill("4900");
    await page.getByLabel("Frequency points").fill("3");
    for (const destination of ["Configuration", "Devices and drivers", "Runs"]) {
      await page
        .getByRole("navigation", { name: "Project sections" })
        .getByRole("button", { name: destination, exact: true })
        .click();
      await page
        .getByRole("navigation", { name: "Project sections" })
        .getByRole("button", { name: "Experiments", exact: true })
        .click();
      await expect(page.getByLabel("Experiment", { exact: true })).toHaveValue(
        "reference_lab.frequency_amplitude",
      );
      await expect(page.getByLabel("Sample ID")).toHaveValue("sample-navigation");
      await expect(page.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue(
        "draft-author",
      );
      await expect(page.getByLabel("Frequency source")).toHaveValue("range");
      await expect(page.getByLabel("Frequency unit")).toHaveValue("MHz");
      await expect(page.getByLabel("Frequency start")).toHaveValue("4700");
      await expect(page.getByLabel("Frequency stop")).toHaveValue("4900");
      await expect(page.getByLabel("Frequency points")).toHaveValue("3");
    }
    expect(submissions).toBe(0);
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    await page.getByLabel("Frequency points").fill("2");
    await expect(page.getByText("Preview ready", { exact: true })).not.toBeVisible();
    await expect(page.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    uv(["python", "-c", CHANGE_DRAFT_CONFIG, project]);
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: /changed; prepare/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
    const shot = testInfo.outputPath("retained-launch-draft.png");
    await page.screenshot({ path: shot, fullPage: true });
    await testInfo.attach("Retained inputs require a new preview", {
      path: shot,
      contentType: "image/png",
    });
    await page.getByRole("button", { name: "Reset launch draft" }).click();
    await expect(page.getByLabel("Frequency", { exact: true })).toHaveValue("4.8");
    await expect(page.getByLabel("Sample ID")).toHaveValue("sample-navigation");
    await expect(page.getByRole("textbox", { name: "Operator", exact: true })).toHaveValue(
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
    uv(["scopecat", "stop", project]);
    // Preserve failed project state and logs for diagnosis.
    if (!failed) await rm(project, { recursive: true, force: true });
  }
});
