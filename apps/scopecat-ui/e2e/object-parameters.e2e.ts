import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { copyAuthorWorkspace, prepareAuthorContexts } from "./author-context";
import type { components } from "../src/api-schema";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    env,
    encoding: "utf8",
    timeout: 30000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
  return result.stdout.trim();
}

test("sample map keeps raw edits and explicitly carries its context through a run and next edit", async ({
  page,
}, testInfo) => {
  test.setTimeout(120000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-object-parameters-"));
  try {
    await copyAuthorWorkspace(home);
    uv(["scopecat", "start", home, "--port", "0", "--static-dir", resolve("dist")]);
    prepareAuthorContexts(uv, home);
    const { base_url: url } = JSON.parse(
      await readFile(join(home, ".scopecat/daemon.json"), "utf8"),
    ) as { base_url: string };
    const health = (await (
      await page.request.get(`${url}/api/v1/health`)
    ).json()) as components["schemas"]["DaemonHealth"];
    const setups = (await (await page.request.get(`${url}/api/v1/setup/definitions`)).json()) as {
      items: components["schemas"]["SetupDefinitionRevision"][];
    };
    const setup = setups.items.find((item) => item.id === "browser-bench-a")!;
    const created = await page.request.post(`${url}/api/v1/samples`, {
      data: {
        operation_id: "object-chip",
        sample_id: "object-chip",
        kind: "chip",
        actor: "browser",
        content: {
          display_name: "Object editor reference chip",
          topology: setup.definition.topology,
        },
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const sample = (await (
      await page.request.get(`${url}/api/v1/samples/object-chip`)
    ).json()) as components["schemas"]["SampleView"];
    const target = await page.request.post(`${url}/api/v1/measurement-targets`, {
      data: {
        catalog_id: health.project_id,
        target_id: "object-target",
        draft: {
          name: "Object editor target",
          actor: "browser",
          content: {
            members: [
              {
                id: "device",
                sample_id: "object-chip",
                revision: sample.revision.revision,
                content_hash: sample.revision.content_hash,
              },
            ],
            connections: [],
          },
        },
      },
    });
    expect(target.ok(), await target.text()).toBe(true);
    const forbidden: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        /experiment-launcher|\/freeze$|\/commit$/.test(request.url())
      )
        forbidden.push(request.url());
    });
    await page.setViewportSize({ width: 1680, height: 1100 });
    await page.goto(`${url}/?sample=object-chip#samples`);
    await page.getByRole("button", { name: "Choose working parameter context" }).click();
    await page.getByLabel("Parameter branch", { exact: true }).fill("browser");
    await page.getByLabel("Setup for capability context").selectOption("browser-bench-a");
    await page
      .getByRole("combobox", { name: "Measurement subject", exact: true })
      .selectOption("object-target:1");
    await page.getByRole("button", { name: "Resolve capability context", exact: true }).click();
    await page
      .getByRole("button", { name: "Edit working parameters beside map", exact: true })
      .click();
    await page.getByRole("button", { name: "Select object a", exact: true }).click();
    const form = page.getByRole("region", { name: "Object parameter values", exact: true });
    const field = form.getByLabel("signals[1].center", { exact: true });
    await expect(field).toBeVisible();
    await field.fill("1e");
    await page.getByRole("button", { name: "Select object b", exact: true }).click();
    await expect(form.getByLabel("signals[2].center", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Select object a", exact: true }).click();
    await expect(field).toHaveValue("1e");
    await expect(page.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    await page.evaluate(`window.scrollTo(0, 0);
      document.querySelector('[aria-label="Edit parameter version"]').scrollTop = 0;`);
    const screenshot = testInfo.outputPath("object-parameter-panel.png");
    await page.screenshot({ path: screenshot });
    await testInfo.attach("Object parameter editor", {
      path: screenshot,
      contentType: "image/png",
    });
    await page.setViewportSize({ width: 1000, height: 900 });
    await form.scrollIntoViewIfNeeded();
    const narrowScreenshot = testInfo.outputPath("object-parameter-panel-narrow.png");
    await page.screenshot({ path: narrowScreenshot });
    await testInfo.attach("Object parameter editor narrow window", {
      path: narrowScreenshot,
      contentType: "image/png",
    });
    await page.getByRole("button", { name: "Configuration", exact: true }).click();
    await expect(page.getByLabel("signals[1].center", { exact: true })).toHaveValue("1e");
    await page.getByRole("button", { name: "Samples", exact: true }).click();
    await page
      .getByRole("button", { name: /Object editor reference chip Available object-chip/ })
      .click();
    await page.getByRole("button", { name: "Select object a", exact: true }).click();
    await expect(form.getByLabel("signals[1].center", { exact: true })).toHaveValue("1e");
    expect(forbidden).toEqual([]);
    const branch = await (
      await page.request.get(`${url}/api/v1/parameters/branches/browser`)
    ).json();
    expect(branch.generation).toBe(1);

    // Only this explicit action carries the resolved target/setup to Experiments.
    await field.fill("5.2");
    await form.getByLabel("signals[1].center unit", { exact: true }).fill("GHz");
    await page
      .getByRole("button", {
        name: "Use working inputs, target and setup for next experiment",
        exact: true,
      })
      .click();
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await expect(page.getByLabel("Experiment setup", { exact: true })).toHaveValue(
      "browser-bench-a",
    );
    await expect(page.getByLabel("Selected registered target")).toContainText(
      "Registered target object-target, revision 1",
    );
    await expect(
      page.getByRole("button", { name: "Start acquisition", exact: true }),
    ).toBeDisabled();
    const previewResponse = page.waitForResponse(
      (item) =>
        item.url().endsWith("/experiment-launcher/preview") && item.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    const preview = await previewResponse;
    expect(preview.status(), await preview.text()).toBe(200);
    const captured = await preview.json();
    expect(captured.reviewed.binding.subject.ref.target_id).toBe("object-target");
    expect(captured.reviewed.config_source.overrides).toHaveLength(1);
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    await expect(page.getByText("experiment: Completed", { exact: true })).toBeVisible();
    const runLink = page.getByRole("link", { name: /^Open retained run:/ });
    const runId = new URL((await runLink.getAttribute("href"))!, page.url()).searchParams.get(
      "run",
    )!;
    await runLink.click();
    await expect(page).toHaveURL(/#runs$/);
    await page.getByRole("button", { name: "Samples", exact: true }).click();
    await page
      .getByRole("button", { name: /Object editor reference chip Available object-chip/ })
      .click();
    await page.getByRole("button", { name: "Select object a", exact: true }).click();
    await expect(field).toHaveValue("5.2");
    await field.fill("5.3");
    await expect(page.getByText("Draft saved in application data", { exact: true })).toBeVisible();
    // The next editing round cannot rewrite the completed run's inputs.
    const evidence = uv([
      "python",
      "-c",
      `
import sys
import scopecat as sc
with sc.open_project(sys.argv[1]).connect() as lab:
    run = lab.get_run(sys.argv[2])
    value = run.config.parameter_snapshot.get('signals').rows[0]['center']
    assert value.to('GHz') == sc.Quantity(5.2, 'GHz'), value
    assert lab.parameters.checkout('browser').head.generation == 1
    print(run.id, value)
`,
      home,
      runId,
    ]);
    await testInfo.attach("adopted-context-run", {
      body: JSON.stringify({ runId, evidence, binding: captured.reviewed.binding }),
      contentType: "application/json",
    });
  } finally {
    uv(["scopecat", "stop", home]);
    await rm(home, { recursive: true, force: true });
  }
});
