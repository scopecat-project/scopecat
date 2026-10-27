import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 60_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(`${result.error?.message ?? ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout.trim();
}
const SEED = `
import json, sys
import scopecat as sc
from scopecat.records.sample import SampleRevisionDraft
project = sc.open_project(sys.argv[1])
project.load_application()
from ui_signal.application import initial_parameters
with project.connect() as lab, project.authoring() as author:
    lab.samples.create("related-chip", kind="synthetic", content=SampleRevisionDraft(display_name="Related chip"))
    content = initial_parameters()
    revision = author.parameters.save(name="related-inputs", catalog=content.catalog, parameters=content.parameters)
    author.parameters.create_branch("related-inputs", revision=revision)
    author.use(parameter_branch="related-inputs", setup=author.setup.get("initial"), sample="related-chip")
    runs = [author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]}).run().wait().result() for _ in range(2)]
    a, b = [lab.get_run(run.id) for run in runs]
    comparison = a.analysis("Pair comparison", key="pair")
    comparison.measurements(id="primary")
    comparison.measurements(b, id="secondary")
    result = comparison.result().fact("score", 1)
    first = result.save()
    assert result.save().id == first.id
    revised = comparison.result().fact("score", 2).save()
    sample = lab.analysis("Chip summary", key="summary", sample="related-chip")
    sample.measurements(a, id="first")
    sample.measurements(b, id="second")
    summary = sample.result().fact("score", 3).save()
    space = lab.analysis("Space summary", key="summary")
    space.measurements(a, id="first")
    space.measurements(b, id="second")
    space_result = space.result().fact("score", 4).save()
    space_revised = space.result().fact("score", 5).save()
    single = a.analysis("Single run summary", key="single")
    single.measurements(id="only")
    single_result = single.result().fact("score", 6).save()
    owner_note = a.analysis("Owner note", key="owner-note").result().fact("note", "no direct inputs").save()
    print(json.dumps({"runs": [a.id, b.id], "ids": [first.id, revised.id, summary.id, space_result.id, space_revised.id], "owner_note":owner_note.id, "single":single_result.id}))
`;

test("finds the same run, sample and space publications from either direct input", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(180_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-related-analyses-"));
  let passed = false;
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    uv([
      "python",
      "-m",
      "scopecat_server.cli",
      "start",
      project,
      "--port",
      "0",
      "--static-dir",
      resolve("dist"),
    ]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const seed = JSON.parse(uv(["python", "-c", SEED, project])) as {
      runs: string[];
      ids: string[];
      owner_note: string;
      single: string;
    };
    const unexpectedWrites: string[] = [];
    context.on("request", (request) => {
      if (["GET", "HEAD", "OPTIONS"].includes(request.method())) return;
      if (request.method() === "POST" && request.url().endsWith("/measurements/query")) return;
      if (
        request.method() === "POST" &&
        request.url().endsWith("/run-comparison") &&
        request.postDataJSON().action === "list"
      )
        return;
      unexpectedWrites.push(`${request.method()} ${request.url()}`);
    });
    // Bound each response through the real endpoint, so navigation exercises multiple pages.
    await page.route("**/api/v1/analysis-publications?*", async (route) => {
      const url = new URL(route.request().url());
      url.searchParams.set("limit", "2");
      await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
    });
    for (const run of seed.runs) {
      await page.goto(`${endpoint.base_url}/?run=${encodeURIComponent(run)}#runs`);
      const header = page.getByTestId("run-detail-header");
      await header.getByText("run 详情", { exact: true }).click();
      const copiedId = await header.locator("code").filter({ hasText: run }).first().textContent();
      expect(copiedId).toBe(run);
      await page.getByRole("button", { name: /Data$/, exact: true }).click();
      await page.getByText("按完整 run ID 查找", { exact: true }).click();
      await page.getByLabel("完整 run ID", { exact: true }).fill(copiedId!);
      // Deliberately exclude all rows; exact ID lookup must still find this run.
      await page.getByLabel("起始时间（本地）", { exact: true }).fill("2099-01-01T00:00");
      await page.getByRole("button", { name: "查找精确 run", exact: true }).click();
      await page.getByRole("button", { name: "打开找到的 run", exact: true }).click();
      const related = page.getByRole("region", { name: "分析结果", exact: true });
      await expect(related.locator(":scope > details > summary")).toHaveCount(2);
      while (
        await related.getByRole("button", { name: "加载更早的分析结果", exact: true }).count()
      ) {
        const previous = await related.locator(":scope > details > summary").count();
        await related.getByRole("button", { name: "加载更早的分析结果", exact: true }).click();
        await expect
          .poll(() => related.locator(":scope > details > summary").count())
          .toBeGreaterThan(previous);
      }
      const expected =
        run === seed.runs[0] ? [...seed.ids, seed.owner_note, seed.single] : seed.ids;
      await expect(related.locator(":scope > details > summary")).toHaveCount(expected.length);
      for (const summary of await related.locator(":scope > details > summary").all())
        await summary.click();
      await expect(related.getByTestId("publication-id")).toHaveCount(expected.length);
      expect((await related.getByTestId("publication-id").allTextContents()).sort()).toEqual(
        [...expected].sort(),
      );
      if (run === seed.runs[0])
        await expect(related.getByText("未声明", { exact: true })).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(
          run === seed.runs[0] ? "results-from-owner.png" : "results-from-input.png",
        ),
        fullPage: true,
      });
    }
    const related = page.getByRole("region", { name: "分析结果", exact: true });
    const row = related
      .locator(":scope > details")
      .filter({ has: page.getByTestId("publication-id").filter({ hasText: seed.ids[0] }) });
    const source = row
      .locator('[aria-label="直接运行输入"]')
      .getByRole("button")
      .filter({ has: page.locator(`[title="运行 ${seed.runs[0]}"]`) });
    await row.getByText("来源与执行证据", { exact: true }).click();
    await source.scrollIntoViewIfNeeded();
    const originalScroll = await page
      .locator("html")
      .evaluate((element) => element.ownerDocument.defaultView!.scrollY);
    await source.click();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[0]}`));
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[1]}`));
    await expect(
      page.getByRole("region", { name: "分析结果", exact: true }).getByTestId("publication-id"),
    ).toHaveCount(seed.ids.length);
    await expect
      .poll(async () =>
        Math.abs(
          (await page
            .locator("html")
            .evaluate((element) => element.ownerDocument.defaultView!.scrollY)) - originalScroll,
        ),
      )
      .toBeLessThan(120);
    await expect(row.getByText("来源与执行证据", { exact: true }).locator("..")).toHaveAttribute(
      "open",
      "",
    );
    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[0]}`));
    await page.goBack();
    const savedVersion = row.getByRole("link", { name: "打开已保存的版本", exact: true });
    await savedVersion.scrollIntoViewIfNeeded();
    const savedVersionScroll = await page
      .locator("html")
      .evaluate((element) => element.ownerDocument.defaultView!.scrollY);
    const popupPromise = context.waitForEvent("page");
    await savedVersion.click({ modifiers: ["Shift"] });
    const popup = await popupPromise;
    await expect(
      popup
        .getByRole("region", { name: "链接的分析结果", exact: true })
        .getByTestId("publication-id"),
    ).toHaveText(seed.ids[0]);
    await popup.close();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[1]}`));
    await savedVersion.click();
    const exactResult = page.getByRole("region", { name: "链接的分析结果", exact: true });
    await expect(exactResult.getByTestId("publication-id")).toHaveText(seed.ids[0]);
    const exactSources = exactResult.getByText("来源与执行证据", { exact: true });
    // Leave in the same native toggle dispatch, without another page click or a 200ms wait.
    const backOnToggle = () =>
      exactSources.locator("..").evaluate((element) => {
        element.addEventListener(
          "toggle",
          () => element.ownerDocument.defaultView!.history.back(),
          { once: true },
        );
      });
    await backOnToggle();
    await exactSources.click();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[1]}`));
    await page.goForward();
    await expect(exactSources.locator("..")).toHaveAttribute("open", "");
    await backOnToggle();
    await exactSources.click();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[1]}`));
    await page.goForward();
    await expect(exactSources.locator("..")).not.toHaveAttribute("open");
    await page.goBack();
    await expect(row.getByText("来源与执行证据", { exact: true }).locator("..")).toHaveAttribute(
      "open",
      "",
    );
    await expect
      .poll(async () =>
        Math.abs(
          (await page
            .locator("html")
            .evaluate((element) => element.ownerDocument.defaultView!.scrollY)) -
            savedVersionScroll,
        ),
      )
      .toBeLessThan(120);
    await page.getByRole("button", { name: /Data$/, exact: true }).click();
    await expect(page.getByLabel("完整 run ID", { exact: true })).toHaveValue(seed.runs[1]);
    await expect(page.getByLabel("起始时间（本地）", { exact: true })).toHaveValue(
      "2099-01-01T00:00",
    );
    await page.goBack();
    await expect(row.getByText("来源与执行证据", { exact: true }).locator("..")).toHaveAttribute(
      "open",
      "",
    );
    await page.screenshot({ path: testInfo.outputPath("run-analysis-return.png"), fullPage: true });

    await page.goto(
      `${endpoint.base_url}/?run=${encodeURIComponent(seed.runs[0])}&run-analysis=${encodeURIComponent(seed.ids[0])}#runs`,
    );
    const pinned = page.getByRole("region", { name: "分析结果", exact: true });
    await expect(pinned.getByTestId("publication-id")).toHaveText(seed.ids[0]);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.locator("html").evaluate((element) => element.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({
      path: testInfo.outputPath("exact-conclusion-narrow.png"),
      fullPage: true,
    });
    // A project publication and a comparison are distinct navigable pages.
    await page.setViewportSize({ width: 1024, height: 600 });
    await pinned.getByText("来源与执行证据", { exact: true }).click();
    await pinned.getByRole("button", { name: seed.runs[1], exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`run=${seed.runs[1]}`));
    await page.goBack();
    await expect(pinned.getByTestId("publication-id")).toHaveText(seed.ids[0]);
    await expect(pinned.getByText("来源与执行证据", { exact: true }).locator("..")).toHaveAttribute(
      "open",
      "",
    );
    await page.getByRole("link", { name: "比较已有 run", exact: true }).click();
    await expect(page.getByLabel("主 run", { exact: true })).toHaveValue(seed.runs[0]);
    await page.goBack();
    await expect(pinned.getByTestId("publication-id")).toHaveText(seed.ids[0]);
    await expect(pinned.getByText("来源与执行证据", { exact: true }).locator("..")).toHaveAttribute(
      "open",
      "",
    );
    await page.goForward();
    await page.getByLabel("对照 run", { exact: true }).selectOption(seed.runs[1]);
    await page.getByLabel("主 run 选点位置", { exact: true }).fill("0,2");
    await page.getByRole("button", { name: "项目分析历史", exact: true }).click();
    await expect(page.getByTestId("publication-id")).toHaveText(seed.ids[4]);
    await page.getByTitle(`Inspect analysis ${seed.ids[3]}`).click();
    await expect(page.getByTestId("publication-id")).toHaveText(seed.ids[3]);
    expect(new URL(page.url()).searchParams.has("compare")).toBe(false);
    await page.getByText("来源与执行证据", { exact: true }).click();
    await page.getByText("Publication details and data management", { exact: true }).click();
    await page.getByRole("button", { name: seed.runs[0], exact: true }).scrollIntoViewIfNeeded();
    const projectScroll = await page
      .locator("html")
      .evaluate((element) => element.ownerDocument.defaultView!.scrollY);
    expect(projectScroll).toBeGreaterThan(100);
    // A separate browser window keeps its own route and selection.
    const other = await context.newPage();
    await other.goto(`${endpoint.base_url}/?run=${seed.runs[1]}`);
    await expect(other.getByTestId("run-detail-header")).toContainText(seed.runs[1]);
    await page.getByRole("button", { name: /Data$/, exact: true }).click();
    await page.goBack();
    await expect(page.getByTestId("publication-id")).toHaveText(seed.ids[3]);
    await expect(page.getByRole("button", { name: "项目分析历史", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect
      .poll(() =>
        page.locator("html").evaluate((element) => element.ownerDocument.defaultView!.scrollY),
      )
      .toBeGreaterThan(projectScroll - 100);
    await page.goForward();
    await expect(page.getByRole("heading", { name: "Data", exact: true })).toBeVisible();
    await page.goBack();
    await page.reload();
    await expect(page.getByTestId("publication-id")).toHaveText(seed.ids[3]);
    await expect(page.getByText("来源与执行证据", { exact: true }).locator("..")).toHaveAttribute(
      "open",
      "",
    );
    await expect
      .poll(() =>
        page.locator("html").evaluate((element) => element.ownerDocument.defaultView!.scrollY),
      )
      .toBeGreaterThan(projectScroll - 100);
    await page.screenshot({
      path: testInfo.outputPath("project-analysis-return.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: seed.runs[0], exact: true }).click();
    await expect(page.getByTestId("run-detail-header")).toContainText(seed.runs[0]);
    await page.goBack();
    await expect(page.getByTestId("publication-id")).toHaveText(seed.ids[3]);
    await page.getByRole("button", { name: "比较已有 run", exact: true }).click();
    await expect(page.getByLabel("对照 run", { exact: true })).toHaveValue(seed.runs[1]);
    await expect(page.getByLabel("主 run 选点位置", { exact: true })).toHaveValue("0,2");
    await page.reload();
    await expect(page.getByLabel("对照 run", { exact: true })).toHaveValue(seed.runs[1]);
    await expect(page.getByLabel("主 run 选点位置", { exact: true })).toHaveValue("0,2");
    await expect(
      page.getByRole("button", { name: "拟合选中数据并保存 analysis", exact: true }),
    ).toBeDisabled();
    await expect(other.getByTestId("run-detail-header")).toContainText(seed.runs[1]);
    await other.close();
    expect(unexpectedWrites).toEqual([]);
    passed = true;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    if (passed) await rm(project, { recursive: true, force: true });
  }
});
