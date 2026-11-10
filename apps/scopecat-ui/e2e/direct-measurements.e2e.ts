import { execFileSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

test("direct VNA collect, select, save, Data and daemon restart preserve the result", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const home = await mkdtemp(join(tmpdir(), "scopecat-direct-journey-"));
  const env = { ...process.env, SCOPECAT_LOCATOR_HOME: join(home, "locators") };
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
    // Collection starts without per-result metadata. Use only the virtual driver.
    const sweep = page.getByTitle("scopecat.network_sweep/v1 / ", { exact: true });
    let collections = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname.endsWith("/collect"))
        collections += 1;
    });
    const firstResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/collect"),
    );
    await sweep.click();
    const firstReceipt = await (await firstResponse).body();
    const current = page.getByRole("region", { name: "当前测量结果", exact: true });
    await expect(current.getByRole("img", { name: /完整曲线/ })).toBeVisible();
    const original = await current.getByText(/完整 \d+ 点/).textContent();
    const recent = page.getByTestId(/^recent-result-/);
    await expect(recent).toHaveCount(1);
    const firstResult = recent.first();
    const firstId = await firstResult.getAttribute("data-testid");
    expect(firstId).toBeTruthy();
    // Changing settings preserves the first result; the second fresh sweep has
    // a different point count so selecting the first cannot pass with stale UI.
    await page.getByRole("spinbutton", { name: /Sweep points/ }).fill("3");
    await page.getByRole("button", { name: "应用暂存修改", exact: true }).click();
    await expect(current.getByRole("img", { name: /完整曲线/ })).toBeVisible();
    await sweep.click();
    await expect(recent).toHaveCount(2);
    await expect(current.getByText(/完整 3 点/)).toBeVisible();
    expect(original).not.toContain("完整 3 点");
    await page.getByTestId(firstId!).click();
    await expect(page.getByTestId(firstId!)).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "收起采集面板", exact: true }).click();
    await expect(current).toBeHidden();
    await page.getByRole("button", { name: "展开采集面板", exact: true }).click();
    await expect(page.getByTestId(firstId!)).toHaveAttribute("aria-pressed", "true");
    await expect(current.getByText(/完整 \d+ 点/)).toHaveText(original!);

    for (const width of [1920, 900]) {
      await page.setViewportSize({ width, height: 1080 });
      const save = page.getByRole("button", { name: "保存所选结果", exact: true });
      await expect(save).toBeInViewport();
      await expect(page.getByTestId(firstId!)).toBeInViewport();
      const chart = current.getByRole("img", { name: /完整曲线/ });
      await expect(chart).toBeVisible();
      const box = await chart.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
      await testInfo.attach(`direct-workbench-${width}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }

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

    // The other result is still unsaved, so normal release remains guarded.
    await page.getByRole("button", { name: "释放控制", exact: true }).click();
    const exit = page.getByRole("dialog", { name: "结束控制前的待办", exact: true });
    await expect(exit.getByRole("heading", { name: "未保存结果：1", exact: true })).toBeVisible();
    await expect(
      exit.getByRole("button", { name: "保持当前状态并结束", exact: true }),
    ).toBeDisabled();
    await exit.getByRole("button", { name: "保存全部未保存结果", exact: true }).click();
    await expect(exit.getByRole("heading", { name: "未保存结果：0", exact: true })).toBeVisible();
    await exit.getByRole("button", { name: "继续控制", exact: true }).click();
    const popup = page.waitForEvent("popup");
    await page.getByRole("link", { name: "查看 Data", exact: true }).click();
    const dataPage = await popup;
    await expect(dataPage).toHaveURL(new RegExp(`measurement=${record.id}.*#history`));
    const data = dataPage.getByRole("region", { name: "直接测量数据", exact: true });
    await expect(data.getByRole("button", { name: /First retained VNA trace/ })).toBeVisible();
    await expect(data.getByRole("img", { name: /完整曲线/ })).toBeVisible();
    const dataPath = new URL(dataPage.url());
    command("stop");
    started = false;
    const restartedUrl = command("start").trim().split("\n").at(-1)!;
    started = true;
    await dataPage.goto(`${restartedUrl}/${dataPath.search}${dataPath.hash}`);
    await expect(data.getByRole("button", { name: /First retained VNA trace/ })).toBeVisible();
    await expect(data.getByRole("img", { name: /完整曲线/ })).toBeVisible();
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
    // the wrapper, before capturing the restarted Data view.
    const savedChart = data.getByRole("img", { name: "s_parameter 完整曲线", exact: true });
    await expect(savedChart.locator("svg")).toBeVisible();
    await expect(savedChart.locator("svg text").filter({ hasText: "frequency" })).toBeVisible();
    await expect(savedChart.locator("svg text").filter({ hasText: "s_parameter" })).toBeVisible();
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
    await testInfo.attach("direct-measurement-data-after-restart", {
      body: await dataPage.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  } finally {
    if (started) command("stop");
    await rm(home, { recursive: true, force: true });
  }
});
