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
import { startProject, stopProject, uv } from "./daemon";
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
  await reviewRetainedExperiment(page);
  await saved(page);
}

test("monitor A remains fixed while independent B and C preserve edits and frozen submission", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-parallel-drafts-"));
  const releasePath = join(project, "release-synthetic-point");
  let started = false;
  const failures: unknown[] = [];
  const interactionTimings: Array<{ action: string; milliseconds: number }> = [];
  try {
    await copyAuthorWorkspace(project);
    const sourcePath = join(project, "src/ui_author/signal.py");
    await writeFile(
      sourcePath,
      (await readFile(sourcePath, "utf8")).replace(
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
    await page.getByRole("button", { name: "Start acquisition", exact: true }).click();
    const current = page.getByRole("link", { name: /View current data:/ });
    await expect(current).toBeVisible();
    const monitorUrl = new URL((await current.getAttribute("href"))!, url);
    const runA = monitorUrl.searchParams.get("run")!;
    const monitor = await context.newPage();
    await monitor.goto(monitorUrl.href);
    await expectRunIdentity(monitor, runA);
    await expect(monitor.getByTestId("run-status")).toHaveText("Running");

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
    await page.reload();
    await selectDraft(page, draftC);
    await expect(gain).toHaveValue("4");
    await page.setViewportSize({ width: 520, height: 800 });
    await page
      .getByRole("region", { name: "Independent experiment drafts", exact: true })
      .scrollIntoViewIfNeeded();
    await expect(
      page.getByRole("combobox", { name: "Editable draft", exact: true }),
    ).toBeInViewport();
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
    await gain.focus();
    await gain.evaluate((node) =>
      (
        node as unknown as { setSelectionRange: (start: number, end: number) => void }
      ).setSelectionRange(0, 1),
    );
    await writeFile(releasePath, "release");
    await expect(monitor.getByTestId("run-status")).toHaveText("Succeeded");
    await expect(gain).toBeFocused();
    await expect(gain).toHaveValue("4");
    expect(
      await gain.evaluate((node) => [
        (node as unknown as { selectionStart: number }).selectionStart,
        (node as unknown as { selectionEnd: number }).selectionEnd,
      ]),
    ).toEqual([0, 1]);
    await testInfo.attach("parallel-draft-identities", {
      body: JSON.stringify({ runA, draftB, draftC, procedureB, requestB: requestB.request_key }),
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
