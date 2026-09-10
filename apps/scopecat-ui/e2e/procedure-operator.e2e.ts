import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
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
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
  return result.stdout.trim();
}

const ADMIT = `
import sys
import scopecat as sc
from scopecat.records.launch_request import LaunchRequest
project = sc.open_project(sys.argv[1])
application = project.load_application()
with project.connect() as lab:
    provider = application.launch_provider
    request = LaunchRequest(action="preview", experiment="temperature", version="1")
    preview = provider(lab, request)
    admitted = provider(lab, LaunchRequest.model_validate({
        **request.model_dump(), "action": "submit", "request_key": "browser-retained",
        "expected_request_hash": preview.request_hash, "config_source": preview.config_source,
                "manual_state": preview.manual_state,
    }))
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
      await page.getByRole("button", { name: /reference_lab.launch_temperature/ }).click();
      await expect(page.getByText("Admitted — not dispatched", { exact: true })).toBeVisible();
      expect(new URL(page.url()).searchParams.get("procedure")).toBe(procedureId);
      const reopenedScreenshot = testInfo.outputPath("operator-reopened.png");
      await page.screenshot({ path: reopenedScreenshot, fullPage: true });
      await testInfo.attach("Reopened procedure", {
        path: reopenedScreenshot,
        contentType: "image/png",
      });
      await page.getByRole("button", { name: "Dispatch existing procedure" }).click();
      await expect(page.getByRole("status").filter({ hasText: /^Completed$/ })).toBeVisible();
      await page.reload();
      await expect(page.getByRole("status").filter({ hasText: /^Completed$/ })).toBeVisible();
      await page.getByRole("link", { name: /^Open retained run:/ }).click();
      await expect(page.getByTestId("run-status")).toHaveText("Succeeded");
      await expect(page.getByText("Measurement data", { exact: true })).toBeVisible();

      const runScreenshot = testInfo.outputPath("operator-retained-run.png");
      await page.screenshot({ path: runScreenshot, fullPage: true });
      await testInfo.attach("Retained run", { path: runScreenshot, contentType: "image/png" });
      await page.goto(`${endpoint.base_url}/#launch`);
      await page.getByLabel("Experiment", { exact: true }).selectOption("channel-timing");
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
      await expect(page.getByText("source: Completed", { exact: true })).toBeVisible();
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
      for (const name of ["daemon.log", "console-worker.log"]) {
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
    original = lab.config.active()
    revised = original.config.model_copy(update={"id": original.config.id + "-draft-context"})
    lab.config.set_default(revised, actor="draft-browser", note="Verify preview invalidation")
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
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("frequency-amplitude");
    await page.getByLabel("Sample ID").fill("sample-navigation");
    await page.getByLabel("Operator", { exact: true }).fill("draft-author");
    await page.getByLabel("Frequency source").selectOption("range");
    await page.getByLabel("Frequency unit").selectOption("MHz");
    await page.getByLabel("Frequency start").fill("4700");
    await page.getByLabel("Frequency stop").fill("4900");
    await page.getByLabel("Frequency points").fill("3");
    for (const destination of ["Configuration", "Instruments", "Runs"]) {
      await page
        .getByRole("navigation", { name: "Project sections" })
        .getByRole("button", { name: destination, exact: true })
        .click();
      await page
        .getByRole("navigation", { name: "Project sections" })
        .getByRole("button", { name: "Experiments", exact: true })
        .click();
      await expect(page.getByLabel("Experiment", { exact: true })).toHaveValue(
        "frequency-amplitude",
      );
      await expect(page.getByLabel("Sample ID")).toHaveValue("sample-navigation");
      await expect(page.getByLabel("Operator", { exact: true })).toHaveValue("draft-author");
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
    await expect(
      page.getByText(/Configuration changed. Editable inputs are retained/),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
    const shot = testInfo.outputPath("retained-launch-draft.png");
    await page.screenshot({ path: shot, fullPage: true });
    await testInfo.attach("Retained inputs require a new preview", {
      path: shot,
      contentType: "image/png",
    });
    await page.getByRole("button", { name: "Reset launch draft" }).click();
    await expect(page.getByLabel("Frequency", { exact: true })).toHaveValue("4.8");
    await expect(page.getByLabel("Sample ID")).toHaveValue("");
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

test("reopens a lost launch receipt after context changes without a second submission", async ({
  page,
}, testInfo) => {
  let failed = false;
  const project = await mkdtemp(join(tmpdir(), "scopecat-draft-submission-"));
  let originalId = "";
  let submissions = 0;
  try {
    for (const name of ["src", "config", "scopecat.toml"])
      await cp(join(ROOT, "examples/reference_lab", name), join(project, name), {
        recursive: true,
      });
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("frequency-amplitude");
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.getByText("Preview ready", { exact: true })).toBeVisible();
    await page.route("**/api/v1/experiment-launcher/submit", async (route) => {
      submissions += 1;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      originalId = ((await response.json()) as { procedure_id: string }).procedure_id;
      await route.abort("failed");
    });
    await page.getByRole("button", { name: "Start acquisition" }).click();
    await expect(
      page.getByRole("heading", { name: "Original submission awaiting confirmation" }),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toBeVisible();
    await page
      .getByRole("navigation", { name: "Project sections" })
      .getByRole("button", { name: "Configuration", exact: true })
      .click();
    uv(["python", "-c", CHANGE_DRAFT_CONFIG, project]);
    await page.route("**/api/v1/experiment-launcher", async (route) => {
      const response = await route.fetch();
      const catalog = (await response.json()) as {
        entries: Array<{ id: string; description: string }>;
      };
      const changed = {
        ...catalog,
        entries: catalog.entries.map((entry) =>
          entry.id === "frequency-amplitude"
            ? { ...entry, description: entry.description + " Updated description." }
            : entry,
        ),
      };
      await route.fulfill({ response, json: changed });
    });
    await page
      .getByRole("navigation", { name: "Project sections" })
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await expect(page.getByText(/Experiment revision changed/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry original submission" })).toBeDisabled();
    await page.getByRole("button", { name: "Check original submission" }).click();
    await page.getByRole("button", { name: "Open submitted procedure" }).click();
    await expect(page).toHaveURL(new RegExp(`procedure=${originalId}`));
    expect(submissions).toBe(1);
    const shot = testInfo.outputPath("original-launch-submission.png");
    await page.screenshot({ path: shot, fullPage: true });
    await testInfo.attach("Original admitted identity recovered read-only", {
      path: shot,
      contentType: "image/png",
    });
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
