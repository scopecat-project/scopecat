import { execFileSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

const identity = ({
  session_id,
  instrument_id,
  command_id,
}: {
  session_id: string;
  instrument_id: string;
  command_id: string;
}) => ({ session_id, instrument_id, command_id });

test("direct VNA collect, select, save, Data and daemon restart preserve the result", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-direct-journey-"));
  const env: NodeJS.ProcessEnv = { ...process.env, SCOPECAT_LOCATOR_HOME: join(home, "locators") };
  delete env.SCOPECAT_DAEMON_URL;
  const command = (action: string, ...args: string[]) =>
    execFileSync(
      "uv",
      [
        "run",
        "--locked",
        "--project",
        "../..",
        "scopecat",
        "app",
        "--action",
        action,
        "--home",
        home,
        ...args,
      ],
      { env, encoding: "utf8", timeout: 60_000 },
    );
  let started = false;
  try {
    command("configure", "--static-dir", resolve("dist"));
    const url = command("start").trim().split("\n").at(-1)!;
    started = true;
    await page.goto(`${url}/#settings`);
    await expect(
      page.getByRole("combobox", { name: "未保存结果暂存方式", exact: true }),
    ).toHaveValue("memory_only");
    await page.getByLabel("操作者名称", { exact: true }).fill("Isolated direct measurement tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: "启用模拟设备体验（无硬件）", exact: true }).click();
    await expect(page.getByText("内置模拟设备（无硬件）", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "添加设备", exact: true }).click();
    const editor = page.getByRole("dialog");
    await editor.getByLabel("设备名称").fill("Isolated synthetic VNA");
    await editor
      .getByRole("combobox", { name: "驱动", exact: true })
      .selectOption("scopecat.virtual.vna");
    await editor.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(editor).toBeHidden();
    await page.getByRole("button", { name: "操作", exact: true }).click();
    await page.getByRole("button", { name: "连接", exact: true }).click();
    await expect(page.getByText("交互会话已连接", { exact: true })).toBeVisible();
    // A modest 1025-point virtual trace exceeds one preview page without
    // allocating a large load. Saved originals must retain all points.
    await page.getByRole("spinbutton", { name: /Sweep points/ }).fill("1025");
    const applyInitial = page.getByRole("button", { name: "应用暂存修改", exact: true });
    await applyInitial.click();
    await expect(applyInitial).toBeDisabled();
    // Collection starts without per-result metadata. Use only the virtual driver.
    const sweep = page.getByTitle("scopecat.network_sweep/v1 / ", { exact: true });
    let collections = 0;
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname.endsWith("/collect-review")
      )
        collections += 1;
    });
    const firstResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/collect-review"),
    );
    await sweep.click();
    const firstSummary = await (await firstResponse).json();
    expect(firstSummary.status).toBe("collected");
    expect(firstSummary).not.toHaveProperty("readback");
    const firstIdentity = identity(firstSummary.pending.selection);
    const firstOriginal = await page.request.post(
      `${url}/api/v1/direct-measurements/pending/receipt`,
      { data: firstIdentity },
    );
    expect(firstOriginal.ok()).toBeTruthy();
    const firstReceipt = await firstOriginal.body();
    expect(firstReceipt.byteLength).toBe(firstSummary.pending.raw_bytes);
    const previewResponse = await page.request.post(
      `${url}/api/v1/direct-measurements/pending/preview`,
      { data: { ...firstIdentity, start: 0, limit: 512 } },
    );
    expect(previewResponse.ok()).toBeTruthy();
    const preview = await previewResponse.json();
    expect(preview.method).toBe("exact_slice");
    const trace = preview.fields.find(
      (field: { request_id: string }) => field.request_id === "s_parameter",
    );
    expect(trace).toMatchObject({
      shape: [1025],
      total_count: 1025,
      returned_count: 512,
      next_start: 512,
      complete: false,
    });
    expect(trace.values).toHaveLength(512);
    expect((await previewResponse.body()).byteLength).toBeLessThanOrEqual(256 * 1024);
    const current = page.getByRole("region", { name: "当前测量结果", exact: true });
    await expect(current.getByRole("img", { name: /局部曲线/ })).toBeVisible();
    const original = await current.getByText(/^s_parameter · (?:完整|局部)/).textContent();
    expect(original).toContain("局部 1–512 / 1025");
    const recent = page.getByTestId(/^recent-result-/);
    await expect(recent).toHaveCount(1);
    const firstResult = recent.first();
    const firstId = await firstResult.getAttribute("data-testid");
    expect(firstId).toBeTruthy();
    // Changing settings preserves the first result; the second fresh sweep has
    // a different point count so selecting the first cannot pass with stale UI.
    await page.getByRole("spinbutton", { name: /Sweep points/ }).fill("3");
    await page.getByRole("button", { name: "应用暂存修改", exact: true }).click();
    await expect(current.getByRole("img", { name: /局部曲线/ })).toBeVisible();
    // A second browser tab shares only this isolated data space's policy.
    // Existing originals stay in their original mode across policy changes.
    const settingsPage = await page.context().newPage();
    await settingsPage.goto(`${url}/#settings`);
    const policy = settingsPage.getByRole("combobox", {
      name: "未保存结果暂存方式",
      exact: true,
    });
    await expect(policy).toHaveValue("memory_only");
    await policy.selectOption("allow_temporary_disk");
    const diskPolicyResponse = settingsPage.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname.endsWith("/pending-result-settings"),
    );
    await settingsPage.getByRole("button", { name: "保存暂存设置", exact: true }).click();
    expect((await diskPolicyResponse).ok()).toBeTruthy();
    await expect(
      settingsPage.getByText("暂存设置已保存，仅对之后的新采集生效。", { exact: true }),
    ).toBeVisible();
    expect(
      (await (await page.request.get(`${url}/api/v1/pending-result-settings`)).json())
        .storage_policy,
    ).toBe("allow_temporary_disk");
    const secondResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/collect-review"),
    );
    await sweep.click();
    const secondSummary = await (await secondResponse).json();
    expect(secondSummary.status).toBe("collected");
    const secondIdentity = identity(secondSummary.pending.selection);
    const secondOriginal = await page.request.post(
      `${url}/api/v1/direct-measurements/pending/receipt`,
      { data: secondIdentity },
    );
    expect(secondOriginal.ok()).toBeTruthy();
    const secondReceipt = await secondOriginal.body();
    expect(secondReceipt.byteLength).toBe(secondSummary.pending.raw_bytes);
    await policy.selectOption("memory_only");
    const memoryPolicyResponse = settingsPage.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname.endsWith("/pending-result-settings"),
    );
    await settingsPage.getByRole("button", { name: "保存暂存设置", exact: true }).click();
    expect((await memoryPolicyResponse).ok()).toBeTruthy();
    await expect(
      settingsPage.getByText("暂存设置已保存，仅对之后的新采集生效。", { exact: true }),
    ).toBeVisible();
    expect(
      (await (await page.request.get(`${url}/api/v1/pending-result-settings`)).json())
        .storage_policy,
    ).toBe("memory_only");
    await settingsPage.close();
    for (const [selection, originalBytes] of [
      [firstIdentity, firstReceipt],
      [secondIdentity, secondReceipt],
    ] as const) {
      const retained = await page.request.post(
        `${url}/api/v1/direct-measurements/pending/receipt`,
        { data: selection },
      );
      expect(retained.ok()).toBeTruthy();
      expect(await retained.body()).toEqual(originalBytes);
    }
    await expect(recent).toHaveCount(2);
    await expect(current.getByText(/^s_parameter · 完整 1–3 \/ 3/)).toBeVisible();
    expect(original).not.toContain("1–3 / 3");
    await page.getByTestId(firstId!).click();
    await expect(page.getByTestId(firstId!)).toHaveAttribute("aria-pressed", "true");
    for (const width of [1920, 900]) {
      await page.setViewportSize({ width, height: 1080 });
      await page.getByRole("button", { name: "收起采集面板", exact: true }).click();
      await expect(current).toBeHidden();
      const layout = page.getByTestId("instrument-workbench-layout");
      const controls = page.getByTestId("instrument-control-panel");
      const collection = page.getByRole("region", { name: "交互采集", exact: true });
      const expand = collection.getByRole("button", { name: "展开采集面板", exact: true });
      await expect(layout).toHaveAttribute("data-collection-collapsed", "true");
      await expect(expand).toBeVisible();
      await expect(collection.getByText("2 条待保存", { exact: true })).toBeVisible();
      await expect(async () => {
        const bounds = await layout.boundingBox();
        const panel = await controls.boundingBox();
        const entry = await collection.boundingBox();
        const button = await expand.boundingBox();
        expect(bounds).not.toBeNull();
        expect(panel).not.toBeNull();
        expect(entry).not.toBeNull();
        expect(button).not.toBeNull();
        // Controls return to the workspace's left edge; do not require them
        // to stretch across the entire screen just to remove the empty column.
        expect(panel!.x).toBeGreaterThanOrEqual(bounds!.x - 1);
        expect(panel!.x - bounds!.x).toBeLessThanOrEqual(24);
        expect(panel!.y).toBeGreaterThanOrEqual(entry!.y + entry!.height - 1);
        expect(entry!.height).toBeLessThanOrEqual(96);
        expect(button!.height).toBeLessThanOrEqual(48);
        expect(panel!.x + panel!.width).toBeLessThanOrEqual(width + 1);
        expect(
          await page.evaluate<number>("document.documentElement.scrollWidth"),
        ).toBeLessThanOrEqual(width);
      }).toPass({ timeout: 5_000 });
      await testInfo.attach(`direct-workbench-collapsed-${width}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
      await expand.click();
      await expect(layout).toHaveAttribute("data-collection-collapsed", "false");
      await expect(recent).toHaveCount(2);
      await expect(page.getByTestId(firstId!)).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByTestId(firstId!)).toContainText("待保存");
      await expect(current.getByText(/^s_parameter · (?:完整|局部)/)).toHaveText(original!);
      await expect(page.getByRole("button", { name: "保存所选结果", exact: true })).toBeEnabled();
      expect(collections).toBe(2);
    }

    for (const width of [1920, 900]) {
      await page.setViewportSize({ width, height: 1080 });
      const save = page.getByRole("button", { name: "保存所选结果", exact: true });
      await expect(save).toBeInViewport();
      await expect(page.getByTestId(firstId!)).toBeInViewport();
      const chart = current.getByRole("img", { name: /局部曲线/ });
      await expect(chart).toBeVisible();
      const box = await chart.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
      await testInfo.attach(`direct-workbench-${width}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }

    // End the synthetic control session without disposing its collected results.
    const originalSession = new URL((await firstResponse).url()).pathname.split("/")[4];
    const closed = await page.request.post(
      `${url}/api/v1/instrument-sessions/${encodeURIComponent(originalSession)}/close`,
    );
    expect(closed.ok()).toBeTruthy();
    // A page reload loses local pending state but not the daemon's original receipts.
    // Recovery must rediscover both original identities without another collect.
    page.once("dialog", (dialog) => void dialog.accept());
    await page.reload();
    await expect(recent).toHaveCount(2);
    await page.getByTestId(firstId!).click();
    await expect(current.getByText(/^s_parameter · (?:完整|局部)/)).toHaveText(original!);
    await expect(current.getByText(/从后台恢复的原采集结果/)).toBeVisible();
    expect(collections).toBe(2);

    // A rejected save followed by a lost successful response both preserve the
    // same command and metadata on retry, without another collection.
    const saves: unknown[] = [];
    let savedId = "";
    await page.route("**/api/v1/direct-measurements", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      saves.push(route.request().postDataJSON());
      if (saves.length === 1)
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "isolated save interruption" }),
        });
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      const record = await response.json();
      savedId = record.id;
      if (saves.length === 2) return route.abort("connectionfailed");
      return route.fulfill({ response });
    });
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    const saveDialog = page.getByRole("dialog", { name: "保存测量", exact: true });
    await saveDialog.getByLabel("标题", { exact: true }).fill("First retained VNA trace");
    await saveDialog.getByLabel("线路名称", { exact: true }).fill("line-a");
    await saveDialog.getByLabel("备注", { exact: true }).fill("Saved after comparing two results");
    await saveDialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(saveDialog.getByRole("button", { name: "重试保存", exact: true })).toBeEnabled();
    await expect(saveDialog.getByLabel("标题", { exact: true })).toBeDisabled();
    await saveDialog.getByRole("button", { name: "重试保存", exact: true }).click();
    await expect(saveDialog.getByRole("button", { name: "重试保存", exact: true })).toBeEnabled();
    await expect(saveDialog.getByRole("alert")).toContainText("保存尚未确认");
    await saveDialog.getByRole("button", { name: "重试保存", exact: true }).click();
    await expect(saveDialog).toBeHidden();
    expect(saves).toHaveLength(3);
    expect(saves[1]).toEqual(saves[0]);
    expect(saves[2]).toEqual(saves[0]);
    expect(collections).toBe(2);
    const savedList = await page.request.get(`${url}/api/v1/direct-measurements`);
    expect((await savedList.json()).items).toHaveLength(1);
    await expect(page.getByTestId(firstId!)).toContainText("已保存");
    const receiptBefore = await page.request.get(
      `${url}/api/v1/direct-measurements/${savedId}/receipt`,
    );
    expect(receiptBefore.ok()).toBeTruthy();
    const receiptBytes = await receiptBefore.body();
    expect(receiptBytes).toEqual(firstReceipt);
    const recordBefore = await page.request.get(`${url}/api/v1/direct-measurements/${savedId}`);
    const record = await recordBefore.json();
    expect(record.selection.title).toBe("First retained VNA trace");
    expect(record.selection.line_label).toBe("line-a");
    expect(record.selection.command_id).toBe(firstId!.replace("recent-result-", ""));

    // The disconnected pending review can save the other original result too.
    await page.getByRole("button", { name: "处理恢复的待保存结果", exact: true }).click();
    const exit = page.getByRole("dialog", { name: "结束控制前的待办", exact: true });
    await expect(exit.getByRole("heading", { name: "未保存结果：1", exact: true })).toBeVisible();
    await expect(
      exit.getByRole("button", { name: "确认待办并关闭窗口", exact: true }),
    ).toBeDisabled();
    await exit.getByRole("button", { name: "保存全部未保存结果", exact: true }).click();
    await expect(exit.getByRole("heading", { name: "未保存结果：0", exact: true })).toBeVisible();
    const bothSaved = await (await page.request.get(`${url}/api/v1/direct-measurements`)).json();
    expect(bothSaved.items).toHaveLength(2);
    const savedSecond = bothSaved.items.find(
      (item: { selection: { command_id: string } }) =>
        item.selection.command_id === secondIdentity.command_id,
    );
    expect(savedSecond).toBeTruthy();
    const savedSecondOriginal = await page.request.get(
      `${url}/api/v1/direct-measurements/${savedSecond.id}/receipt`,
    );
    expect(savedSecondOriginal.ok()).toBeTruthy();
    expect(await savedSecondOriginal.body()).toEqual(secondReceipt);
    await exit.getByRole("button", { name: "返回工作台（保留待核查状态）", exact: true }).click();
    const controlUrl = page.url();
    const popup = page.waitForEvent("popup");
    await page.getByRole("link", { name: "查看已保存结果", exact: true }).click();
    const resultPage = await popup;
    await expect(resultPage).toHaveURL(new RegExp(`saved-measurement=${record.id}`));
    const savedResult = resultPage.getByRole("main", { name: "已保存测量结果", exact: true });
    await expect(
      savedResult.getByRole("heading", { name: "First retained VNA trace", level: 1 }),
    ).toBeVisible();
    await expect(savedResult.getByRole("img", { name: /局部曲线/ })).toBeVisible();
    await expect(resultPage.getByRole("navigation")).toHaveCount(0);
    await expect(savedResult.getByRole("textbox", { name: "追加备注", exact: true })).toHaveCount(
      0,
    );
    await expect(
      savedResult.getByRole("button", { name: "追加标记（不修改原结果）", exact: true }),
    ).toHaveCount(0);
    await expect(
      resultPage.getByRole("textbox", { name: "搜索已加载记录", exact: true }),
    ).toHaveCount(0);
    await expect(page).toHaveURL(controlUrl);
    await expect(page.getByTestId(firstId!)).toHaveAttribute("aria-pressed", "true");
    await resultPage.reload();
    await expect(
      savedResult.getByRole("heading", { name: "First retained VNA trace", level: 1 }),
    ).toBeVisible();
    await expect(savedResult.getByRole("img", { name: /局部曲线/ })).toBeVisible();

    // Historical browsing remains in the main application, separate from the
    // single-result browser tab. The original control session is already closed.
    await expect(page.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: /Data$/ }).click();
    await page.getByRole("button", { name: "直接测量", exact: true }).click();
    const history = page.getByRole("region", { name: "直接测量数据", exact: true });
    await history
      .getByRole("textbox", { name: "搜索已加载记录", exact: true })
      .fill("First retained VNA trace");
    await history.getByRole("button", { name: /First retained VNA trace/ }).click();
    await expect(page).toHaveURL(new RegExp(`measurement=${record.id}.*#history`));
    await expect(history.getByRole("img", { name: /局部曲线/ })).toBeVisible();
    const resultPath = new URL(resultPage.url());
    command("stop");
    started = false;
    const restartedUrl = command("start").trim().split("\n").at(-1)!;
    started = true;
    await resultPage.goto(`${restartedUrl}/${resultPath.search}${resultPath.hash}`);
    await expect(
      savedResult.getByRole("heading", { name: "First retained VNA trace", level: 1 }),
    ).toBeVisible();
    await expect(savedResult.getByRole("img", { name: /局部曲线/ })).toBeVisible();
    const receiptAfter = await page.request.get(
      `${restartedUrl}/api/v1/direct-measurements/${record.id}/receipt`,
    );
    expect(receiptAfter.ok()).toBeTruthy();
    expect(await receiptAfter.body()).toEqual(receiptBytes);
    const recordAfter = await page.request.get(
      `${restartedUrl}/api/v1/direct-measurements/${record.id}`,
    );
    expect(await recordAfter.json()).toEqual(record);
    expect(collections).toBe(2);
    // The accessible chart wrapper exists before lazy ECharts finishes drawing.
    // Wait for rendered coordinates and an actual multi-point series, not just
    // the wrapper, before capturing the restarted single-result view.
    const savedChart = savedResult.getByRole("img", { name: "s_parameter 局部曲线", exact: true });
    await expect(savedChart.locator("svg")).toBeVisible();
    await expect(savedChart.locator("svg text").filter({ hasText: "frequency" })).toBeVisible();
    await expect(savedChart.locator("svg text").filter({ hasText: "ratio" })).toBeVisible();
    await expect
      .poll(() =>
        savedChart.locator("svg").evaluate((svg) =>
          [...svg.querySelectorAll("path")].some((path) => {
            const bounds = path.getBoundingClientRect();
            const commands = path.getAttribute("d") ?? "";
            return (
              (commands.match(/[Ll]/g)?.length ?? 0) > 10 && bounds.width > 100 && bounds.height > 1
            );
          }),
        ),
      )
      .toBe(true);
    await testInfo.attach("direct-measurement-result-after-restart", {
      body: await resultPage.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  } finally {
    if (started) command("stop");
    await rm(home, { recursive: true, force: true });
  }
});
