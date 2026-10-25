import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  chooseAuthorContext,
  copyAuthorWorkspace,
  prepareAuthorContexts,
  reviewRetainedExperiment,
} from "./author-context";
import { ROOT, startProject, stopProject, uv } from "./daemon";
import { expectRunIdentity } from "./run-identity";

async function nameDraft(page: Page, name: string) {
  await page.getByRole("button", { name: "Rename draft", exact: true }).click();
  await page.getByLabel("Draft name", { exact: true }).fill(name);
  await page.getByRole("button", { name: "Save name", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Editable draft", exact: true }).locator("option:checked"),
  ).toHaveText(name);
}

async function saved(page: Page) {
  await expect(
    page.getByText("Experiment input saved in application data.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy to new draft", exact: true })).toBeEnabled();
}

async function selectDraft(page: Page, id: string) {
  const picker = page.getByRole("combobox", { name: "Editable draft", exact: true });
  await picker.focus();
  await expect(picker.locator(`option[value="${id}"]`)).toBeAttached();
  await picker.selectOption(id);
  await expect(picker).toHaveValue(id);
  await reviewRetainedExperiment(page);
  await saved(page);
}

// Use the existing virtual thermometer through ordinary compilation and resource admission.
// Pure compute has no resource requirement and cannot establish a real queue wait.
async function addSharedVirtualResource(project: string) {
  const infrastructure = JSON.parse(
    await readFile(join(ROOT, "testing/reference_lab/config/system-infrastructure.json"), "utf8"),
  );
  const instrument = infrastructure.instrument_registry.instruments.find(
    (item: { id: string }) => item.id === "mixing-chamber",
  );
  const route = infrastructure.routing.routes.find(
    (item: { id: string }) => item.id === "mixing-chamber",
  );
  expect(instrument.connection.kind).toBe("virtual");
  const applicationPath = join(project, "src/ui_author/application.py");
  const application = await readFile(applicationPath, "utf8");
  await writeFile(
    applicationPath,
    application
      .replace("from pathlib import Path", "from pathlib import Path\nimport json")
      .replace(
        'entities=[sc.EntityRef(id=id, kind="signal") for id in ("a", "b")]',
        'entities=[sc.EntityRef(id=id, kind="signal") for id in ("a", "b")] + [sc.EntityRef(id="cryostat", kind="cryostat")]',
      )
      .replace(
        "InstrumentRegistry(instruments=[])",
        `InstrumentRegistry.model_validate(json.loads(${JSON.stringify(JSON.stringify({ instruments: [instrument] }))}))`,
      )
      .replace(
        "routing=RoutingGraph()",
        `routing=RoutingGraph.model_validate(json.loads(${JSON.stringify(JSON.stringify({ routes: [route] }))}))`,
      ),
  );
  await writeFile(
    join(project, "src/ui_author/backend.py"),
    "from pathlib import Path\nfrom scopecat.sdk.instruments import InstrumentBackend\n\ndef create_backend(root: Path) -> InstrumentBackend:\n    from reference_lab.backend import create_backend as reference_backend\n    return reference_backend(root)\n",
  );
  const manifest = join(project, "scopecat.toml");
  await writeFile(
    manifest,
    (await readFile(manifest, "utf8")).replace(
      "[authors]",
      'instrument_backend = "ui_author.backend:create_backend"\n\n[authors]',
    ),
  );
}

test("monitor A remains fixed while independent B and C preserve edits and frozen submission", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  await page.setViewportSize({ width: 1266, height: 893 });
  const captureWorkbench = async (name: string) => {
    await page.evaluate("window.scrollTo(0, 0)");
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`) });
  };
  const project = await mkdtemp(join(tmpdir(), "scopecat-parallel-drafts-"));
  const releasePath = join(project, "release-synthetic-point");
  let started = false;
  const failures: unknown[] = [];
  const interactionTimings: Array<{ action: string; milliseconds: number }> = [];
  try {
    await copyAuthorWorkspace(project);
    await addSharedVirtualResource(project);
    const sourcePath = join(project, "src/ui_author/signal.py");
    await writeFile(
      sourcePath,
      (await readFile(sourcePath, "utf8"))
        .replace(
          "import scopecat as sc",
          "import scopecat as sc\nfrom scopecat_instruments import temperature_readout",
        )
        .replace(
          'gain: float, polarity: str = "positive"',
          'gain: float, temperature: sc.Quantity, polarity: str = "positive"',
        )
        .replace(
          "            polarity=polarity,",
          "            polarity=polarity,\n            temperature=sample.temperature,",
        )
        .replace(
          "    return cast(\n",
          '    sample = temperature_readout(experiment, for_=sc.one(sc.EntityRef(id="cryostat", kind="cryostat"))).sample()\n    return cast(\n',
        )
        .replace(
          "    detuning =",
          `    if frequency.to("GHz").value > 4.8:\n        import time\n        from pathlib import Path\n        deadline = time.monotonic() + 120\n        while not Path(${JSON.stringify(releasePath)}).exists():\n            if time.monotonic() > deadline:\n                raise TimeoutError("Browser did not release synthetic point")\n            time.sleep(0.05)\n    detuning =`,
        ),
    );
    const url = await startProject(project);
    started = true;
    prepareAuthorContexts(uv, project);
    await page.goto(`${url}/#settings`);
    await page.getByLabel("Operator name", { exact: true }).fill("Parallel draft tester");
    await page.getByRole("button", { name: "Save operator", exact: true }).click();
    await expect(page.getByText("Operator saved", { exact: true })).toBeVisible();
    await page.goto(`${url}/#launch`);
    await page.getByLabel("Experiment", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    await nameDraft(page, "A");
    await page.getByLabel("Gain", { exact: true }).fill("1");
    await page.getByLabel("Frequency source").selectOption("range");
    await page.getByLabel("Frequency start").fill("4.8");
    await page.getByLabel("Frequency stop").fill("4.9");
    await page.getByLabel("Frequency points").fill("2");
    await saved(page);
    await expect(
      page.getByText("Author code refreshed.", {
        exact: true,
      }),
    ).toBeVisible();
    await captureWorkbench("editor-before-start");
    await page.screenshot({ path: testInfo.outputPath("editor-ready-full.png"), fullPage: true });
    await expect(page.getByLabel("Gain", { exact: true })).toBeInViewport();
    await expect(page.getByLabel("Frequency start", { exact: true })).toBeInViewport();
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const current = page.getByRole("link", { name: /View current data:/ });
    await expect(current).toBeVisible();
    await expect(page.getByText("Running · 1 completed points", { exact: true })).toBeVisible();
    await captureWorkbench("monitor-selected-running");
    await page.screenshot({
      path: testInfo.outputPath("monitor-selected-running-full.png"),
      fullPage: true,
    });
    const monitorUrl = new URL((await current.getAttribute("href"))!, url);
    const runA = monitorUrl.searchParams.get("run")!;
    const procedureA = monitorUrl.searchParams.get("procedure")!;
    const monitor = await context.newPage();
    monitorUrl.searchParams.set("focus", "run");
    await monitor.goto(monitorUrl.href);
    await expect(monitor.getByRole("navigation", { name: "Project sections" })).toHaveCount(0);
    await expectRunIdentity(monitor, runA);
    await expect(monitor.getByTestId("run-status")).toHaveText("Running");
    // Identity inspection opens metadata; return to the normal collapsed monitor.
    await monitor.getByText("Run details", { exact: true }).click();
    for (const width of [1266, 390]) {
      await monitor.setViewportSize({ width, height: 900 });
      await monitor.evaluate(() => window.scrollTo(0, 0));
      await expect(monitor.getByRole("region", { name: "Run progress" })).toBeInViewport();
      await expect(
        monitor.getByRole("button", { name: "Focus chart", exact: true }),
      ).toBeInViewport();
      const chart = monitor.getByTestId("measurement-charts").locator("svg").first();
      await expect(chart).toBeInViewport();
      expect(
        await monitor.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(width);
      await monitor.screenshot({
        path: testInfo.outputPath(`curve-first-${width}.png`),
        fullPage: true,
      });
    }
    await monitor.setViewportSize({ width: 1266, height: 893 });
    await monitor.getByRole("link", { name: "Return to preparation", exact: true }).click();
    await expect(monitor.getByRole("navigation", { name: "Project sections" })).toBeVisible();
    await monitor.setViewportSize({ width: 520, height: 800 });
    await monitor.getByRole("button", { name: /Open navigation$/ }).click();
    await expect(monitor.getByRole("dialog", { name: "Workspace navigation" })).toBeVisible();
    await monitor.goBack();
    await expect(monitor.getByRole("navigation", { name: "Project sections" })).toHaveCount(0);
    await expect(monitor.locator("#workspace-body")).toHaveJSProperty("inert", false);
    await expect(monitor.getByRole("button", { name: /Navigation/ })).toBeFocused();
    await monitor.screenshot({ path: testInfo.outputPath("focused-back-from-open-drawer.png") });
    await monitor.goForward();
    await expect(monitor.getByRole("dialog", { name: "Workspace navigation" })).toHaveCount(0);
    await expect(monitor.locator("#workspace-body")).toHaveJSProperty("inert", false);
    await monitor.setViewportSize({ width: 1266, height: 893 });
    await expect(monitor.getByRole("navigation", { name: "Project sections" })).toBeVisible();
    await monitor.reload();
    await expect(monitor.getByRole("navigation", { name: "Project sections" })).toBeVisible();
    await monitor.goBack();
    await expectRunIdentity(monitor, runA);
    await monitor.getByRole("link", { name: "Compare retained runs", exact: true }).click();
    await expect(monitor.getByRole("navigation", { name: "Project sections" })).toBeVisible();
    await monitor.goBack();
    await expect(monitor.getByRole("navigation", { name: "Project sections" })).toHaveCount(0);

    await page.getByRole("button", { name: "Editor", exact: true }).click();
    await expect(page.getByRole("region", { name: "Selected execution", exact: true })).toHaveCount(
      0,
    );
    await saved(page);
    await page.getByRole("button", { name: "Copy to new draft", exact: true }).click();
    await reviewRetainedExperiment(page);
    await nameDraft(page, "B");
    const draftB = await page
      .getByRole("combobox", { name: "Editable draft", exact: true })
      .inputValue();
    await page.getByLabel("Gain", { exact: true }).fill("2");
    await saved(page);
    await page.getByRole("button", { name: "Copy to new draft", exact: true }).click();
    await reviewRetainedExperiment(page);
    await nameDraft(page, "C");
    const draftC = await page
      .getByRole("combobox", { name: "Editable draft", exact: true })
      .inputValue();
    expect(draftC).not.toBe(draftB);
    await page.getByLabel("Gain", { exact: true }).fill("3");
    await saved(page);

    // A remains running while C retains its own raw text, selection and focus.
    const gain = page.getByLabel("Gain", { exact: true });
    await gain.focus();
    await gain.evaluate((node) =>
      (
        node as unknown as { setSelectionRange: (start: number, end: number) => void }
      ).setSelectionRange(0, 1),
    );
    await expect(monitor.getByTestId("run-status")).toHaveText("Running");
    await expect(gain).toBeFocused();
    await expect(gain).toHaveValue("3");
    expect(
      await gain.evaluate((node) => [
        (node as unknown as { selectionStart: number }).selectionStart,
        (node as unknown as { selectionEnd: number }).selectionEnd,
      ]),
    ).toEqual([0, 1]);
    await expectRunIdentity(monitor, runA);

    await page.getByRole("button", { name: /Data$/, exact: true }).click();
    await expect(page.getByRole("button", { name: /Data$/, exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.getByRole("button", { name: /Experiments$/, exact: true }).click();
    await expect(gain).toHaveValue("3");
    await expect(gain).toBeFocused();
    await page.getByRole("button", { name: /Collapse navigation$/ }).click();
    await expect(page.locator("html")).toHaveClass(/navigation-compact/);
    await expect(page.getByLabel("Decision attention")).toBeVisible();
    await expect(page.getByLabel("Decision attention")).toHaveText("0 待处理");
    await page.screenshot({ path: testInfo.outputPath("compact-sidebar-preserved-editor.png") });
    await page.getByRole("button", { name: /Expand navigation$/ }).click();
    const switchStarted = performance.now();
    await selectDraft(page, draftB);
    await expect(gain).toHaveValue("2");
    interactionTimings.push({
      action: "switch C to B and review retained input",
      milliseconds: performance.now() - switchStarted,
    });
    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/experiment-launcher/submit") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const response = await submitted;
    expect(response.status(), await response.text()).toBe(200);
    const requestB = response.request().postDataJSON();
    expect(requestB.control_edits.gain).toEqual({ mode: "fixed", value: 2 });
    const procedureB = (await response.json()).procedure_id;
    await expect(
      page.getByRole("heading", { name: "Submission confirmed", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Submitted work", exact: true }).click();
    await expect(page.getByRole("button").filter({ hasText: procedureB })).toBeVisible();
    await captureWorkbench("confirmed-submitted-work");
    const retainedBeforeEdit = (
      await (await page.request.get(`${url}/api/v1/launch-attempts`)).json()
    ).items.find(
      (item: { request: { request_key: string } }) =>
        item.request.request_key === requestB.request_key,
    ).request;
    await page.getByRole("button", { name: "Editor", exact: true }).click();
    await selectDraft(page, draftC);
    await expect(gain).toHaveValue("3");
    await gain.fill("4");
    await saved(page);
    const attempts = (await (await page.request.get(`${url}/api/v1/launch-attempts`)).json()).items;
    const frozenB = attempts.find(
      (item: { request: { request_key: string } }) =>
        item.request.request_key === requestB.request_key,
    );
    expect(frozenB.request).toEqual(retainedBeforeEdit);
    expect(frozenB.request.control_edits.gain).toMatchObject({ mode: "fixed", value: 2 });
    expect(attempts).toHaveLength(2);
    await expectRunIdentity(monitor, runA);
    const savedDraftLink = await page
      .getByRole("link", { name: "Reopen this saved draft", exact: true })
      .getAttribute("href");
    const reopened = await context.newPage();
    await reopened.goto(new URL(savedDraftLink!, url).href);
    await expect(
      reopened.getByRole("combobox", { name: "Editable draft", exact: true }),
    ).toHaveValue(draftC);
    await expect(reopened.getByLabel("Gain", { exact: true })).toHaveValue("4");
    await expect(
      reopened.getByRole("button", { name: "Confirm reviewed input", exact: true }),
    ).toBeVisible();
    await reopened.screenshot({ path: testInfo.outputPath("saved-draft-c-link.png") });
    await reopened.close();

    await page
      .getByText("Recover experiment input or an original submission", { exact: true })
      .click();
    await page.getByRole("button", { name: "Refresh recovery history", exact: true }).click();
    await page
      .getByRole("button", { name: `Recover original submission ${frozenB.sequence}`, exact: true })
      .click();
    await page.getByText("Compare submitted request with current draft", { exact: true }).click();
    const comparison = page.getByRole("table", {
      name: "Selected submitted request and current draft comparison",
    });
    const gainComparison = comparison
      .getByRole("row")
      .filter({ has: page.getByRole("rowheader", { name: /Control · gain/ }) });
    await expect(gainComparison.getByRole("cell")).toHaveText(["Fixed · 2", "Fixed · 4"]);
    await comparison.screenshot({ path: testInfo.outputPath("frozen-b-current-c-comparison.png") });

    await page.getByRole("button", { name: "Submitted work", exact: true }).click();
    await captureWorkbench("submitted-work");
    await page.getByRole("button").filter({ hasText: procedureB }).click();
    const inspection = page.getByRole("region", { name: "Submitted work inspection", exact: true });
    await expect(
      inspection.getByRole("heading", { name: `Submitted task ${procedureB}`, exact: true }),
    ).toBeVisible();
    await expect(inspection.getByText("Waiting for resources", { exact: true })).toBeVisible();
    const ownerLink = inspection.getByRole("link", { name: "another run", exact: true });
    await expect(ownerLink).toBeVisible();
    const ownerUrl = new URL((await ownerLink.getAttribute("href"))!, url);
    const savedDraftUrl = new URL(savedDraftLink!, url);
    expect(ownerUrl.origin).toBe(new URL(url).origin);
    expect(ownerUrl.hash).toBe("#runs");
    expect(Object.fromEntries(ownerUrl.searchParams)).toEqual({
      procedure: procedureB,
      run: runA,
      workspace: savedDraftUrl.searchParams.get("workspace"),
      experiment: "signal",
      draft: draftC,
    });
    await expect(page).toHaveURL(
      (currentUrl) => currentUrl.searchParams.get("procedure") === procedureA,
    );
    await expectRunIdentity(monitor, runA);
    await inspection
      .locator("summary")
      .filter({ hasText: /^Cancel queued task$/ })
      .click();
    await inspection.screenshot({
      path: testInfo.outputPath("submitted-b-waiting-owner-actions.png"),
    });
    await inspection
      .getByLabel("Cancellation reason", { exact: true })
      .fill("Cancel queued B while A keeps running");
    await inspection.getByRole("button", { name: "Cancel queued task", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`${url}/api/v1/procedures/${procedureB}/operator`)).json())
            .procedure.closure?.status,
      )
      .toBe("cancelled");
    const cancelledB = await (
      await page.request.get(`${url}/api/v1/procedures/${procedureB}/operator`)
    ).json();
    expect(cancelledB.child_runs[0].run.control.completed_point_count).toBe(0);
    await expect(monitor.getByTestId("run-status")).toHaveText("Running");
    await page.getByRole("button").filter({ hasText: procedureA }).click();
    await inspection
      .locator("summary")
      .filter({ hasText: /^Cancel remaining procedure$/ })
      .click();
    await inspection
      .getByLabel("Cancellation reason", { exact: true })
      .fill("Stop A after its current synthetic point");
    await inspection.getByRole("button", { name: "Stop after current step", exact: true }).click();
    await expect(inspection.getByText(/not a completed stop/)).toBeVisible();
    await expect(monitor.getByTestId("run-status")).toHaveText("Running");
    await page.getByRole("button", { name: "Editor", exact: true }).click();
    await expect(gain).toHaveValue("4");
    await page.setViewportSize({ width: 520, height: 800 });
    await page
      .getByRole("region", { name: "Independent experiment drafts", exact: true })
      .scrollIntoViewIfNeeded();
    await expect(
      page.getByRole("combobox", { name: "Editable draft", exact: true }),
    ).toBeInViewport();
    await page.getByRole("button", { name: /Open navigation$/ }).click();
    await expect(page.getByRole("dialog", { name: "Workspace navigation" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("narrow-sidebar-drawer.png") });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: /Open navigation$/ })).toBeFocused();
    await page.getByRole("button", { name: /Open navigation$/ }).click();
    await page.setViewportSize({ width: 1266, height: 893 });
    await expect(page.getByRole("dialog", { name: "Workspace navigation" })).toHaveCount(0);
    await expect(page.locator("#workspace-body")).toHaveJSProperty("inert", false);
    await gain.focus();
    await expect(gain).toBeFocused();
    await page.setViewportSize({ width: 520, height: 800 });
    const narrowWidths = {
      viewport: page.viewportSize()!.width,
      document: await page.locator("html").evaluate((node) => node.scrollWidth),
    };
    expect(narrowWidths.document).toBeLessThanOrEqual(narrowWidths.viewport);
    await testInfo.attach("interaction-sample", {
      body: JSON.stringify({ samples: 1, interactionTimings, narrowWidths }),
      contentType: "application/json",
    });
    await page.screenshot({ path: testInfo.outputPath("independent-draft-c.png") });
    await monitor.screenshot({ path: testInfo.outputPath("fixed-monitor-a.png") });
    await page
      .getByRole("region", { name: "Inputs for this run", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("narrow-fields.png") });
    await page
      .getByRole("region", { name: "Preview and start", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("narrow-actions.png") });
    await gain.focus();
    await gain.evaluate((node) =>
      (
        node as unknown as { setSelectionRange: (start: number, end: number) => void }
      ).setSelectionRange(0, 1),
    );
    await writeFile(releasePath, "release");
    await expect(monitor.getByTestId("run-status")).toHaveText("Succeeded");
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`${url}/api/v1/procedures/${procedureA}/operator`)).json())
            .procedure.closure?.status,
      )
      .toBe("cancelled");
    await expect(gain).toBeFocused();
    await expect(gain).toHaveValue("4");
    expect(
      await gain.evaluate((node) => [
        (node as unknown as { selectionStart: number }).selectionStart,
        (node as unknown as { selectionEnd: number }).selectionEnd,
      ]),
    ).toEqual([0, 1]);
    await monitor.screenshot({ path: testInfo.outputPath("completed-monitor-a.png") });
    await testInfo.attach("parallel-draft-identities", {
      body: JSON.stringify({
        runA,
        procedureA,
        draftB,
        draftC,
        procedureB,
        requestB: requestB.request_key,
        resource: "mixing-chamber",
      }),
      contentType: "application/json",
    });
  } catch (error) {
    failures.push(error);
  }
  try {
    await writeFile(releasePath, "release");
    if (started) stopProject(project);
  } catch (error) {
    failures.push(error);
  }
  if (failures.length)
    throw new AggregateError(failures, "Parallel draft journey or cleanup failed");
  await rm(project, { recursive: true, force: true });
});
