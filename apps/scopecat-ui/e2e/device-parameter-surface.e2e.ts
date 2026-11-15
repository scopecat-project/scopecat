import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cleanDeviceFixture, startDeviceFixture } from "./device-fixture";
import { expect, test } from "@playwright/test";
import type { components } from "../src/api-schema";
type DeviceView = components["schemas"]["DeviceView"];
const deviceName = "Parameter layout virtual VNA";
test("parameter sheet keeps narrow two-column controls and fixed actions usable", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const root = await mkdtemp(join(tmpdir(), "scopecat-parameter-layout-"));
  const fixture = startDeviceFixture(
    "../../packages/scopecat-server/tests/fixtures/device_curve_daemon.py",
    root,
  );
  let passed = false;
  let url = "";
  try {
    await expect
      .poll(
        async () => {
          if (fixture.child.exitCode !== null) throw new Error(fixture.logs);
          try {
            url = await readFile(join(root, "url"), "utf8");
            return (await page.request.get(`${url}/api/v1/health`)).ok();
          } catch {
            return false;
          }
        },
        { timeout: 30_000 },
      )
      .toBe(true);
    await page.goto(`${url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("layout-tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${url}/#instruments`);
    await page.getByRole("button", { name: /Devices and drivers$/, exact: true }).click();
    await page.getByRole("button", { name: "添加设备", exact: true }).click();
    const editor = page.getByRole("dialog");
    await editor.getByLabel("设备名称").fill(deviceName);
    await editor.getByRole("button", { name: "保存设备", exact: true }).click();
    await expect(editor).toBeHidden();
    const devices: DeviceView[] = (await (await page.request.get(`${url}/api/v1/devices`)).json())
      .items;
    const controlUrl = `${url}/?device-control=${encodeURIComponent(devices[0].device.id)}`;
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(controlUrl);

    await page.getByRole("button", { name: "连接", exact: true }).click();
    await expect(page.getByText("已连接", { exact: true })).toBeVisible();
    const workbench = page.getByTestId("instrument-workbench-layout");
    await expect(workbench).toHaveAttribute("data-control-collapsed", "false");
    await expect(workbench).toHaveAttribute("data-collection-collapsed", "false");
    await expect(page.getByRole("region", { name: "采集结果工作区", exact: true })).toBeVisible();
    await expect(page.getByRole("separator", { name: "调整控制与结果区域大小" })).toBeVisible();
    await expect(page.getByRole("button", { name: "保存所选结果", exact: true })).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath("default-capable-empty-split.png"),
      fullPage: true,
    });
    await expect(page.getByRole("button", { name: "刷新状态", exact: true })).toBeInViewport({
      ratio: 1,
    });
    await expect(page.getByRole("button", { name: "Acquire sweep", exact: true })).toBeInViewport({
      ratio: 1,
    });
    await expect(
      page.getByRole("button", { name: "Read existing trace (no trigger)", exact: true }),
    ).toBeInViewport({ ratio: 1 });
    await page.getByRole("button", { name: "收起结果", exact: true }).click();
    for (const [width, height] of [
      [1920, 1080],
      [1200, 750],
      [560, 750],
      [390, 600],
      [760, 600],
    ]) {
      await page.setViewportSize({ width, height });
      const panel = page.getByTestId("instrument-control-panel");
      await expect(panel).toBeVisible();
      await expect(async () => {
        const layout = await panel.evaluate((element) => {
          const scroll = element.closest(".device-pane-scroll")!;
          const rect = scroll.getBoundingClientRect();
          const grids = [...element.querySelectorAll(".device-parameter-grid")].map((grid) => ({
            width: grid.getBoundingClientRect().width,
            columns: element.ownerDocument
              .defaultView!.getComputedStyle(grid)
              .gridTemplateColumns.split(" ").length,
            overflow: grid.scrollWidth - grid.clientWidth,
          }));
          const numberInputs = [...element.querySelectorAll('input[type="number"]')].map(
            (input) => {
              const style = element.ownerDocument.defaultView!.getComputedStyle(input);
              const context = element.ownerDocument.createElement("canvas").getContext("2d")!;
              context.font = `${style.fontSize} ${style.fontFamily}`;
              const value = (input as { value: string }).value;
              return {
                value,
                needed:
                  context.measureText(value).width +
                  parseFloat(style.paddingLeft) +
                  parseFloat(style.paddingRight),
                available: input.clientWidth,
              };
            },
          );
          const actions = element.ownerDocument
            .querySelector(".device-parameter-actions")!
            .getBoundingClientRect();
          return {
            scrollHeight: rect.height,
            scrollBottom: rect.bottom,
            actionsTop: actions.top,
            actionsBottom: actions.bottom,
            viewport: element.ownerDocument.defaultView!.innerHeight,
            overflow:
              element.ownerDocument.documentElement.scrollWidth -
              element.ownerDocument.defaultView!.innerWidth,
            grids,
            numberInputs,
          };
        });
        expect(layout.scrollHeight).toBeGreaterThanOrEqual(160);
        expect(layout.scrollBottom).toBeLessThanOrEqual(layout.actionsTop + 1);
        expect(layout.actionsBottom).toBeLessThanOrEqual(layout.viewport);
        expect(layout.overflow).toBeLessThanOrEqual(1);
        for (const input of layout.numberInputs) {
          expect(input.needed, input.value).toBeLessThanOrEqual(input.available);
        }
        for (const grid of layout.grids) {
          expect(grid.overflow).toBeLessThanOrEqual(1);
          const expectedColumns =
            grid.width >= 1280 ? 4 : grid.width >= 780 ? 3 : grid.width >= 240 ? 2 : 1;
          expect(grid.columns).toBe(expectedColumns);
          if (width >= 1200) expect(grid.columns).toBeGreaterThanOrEqual(3);
        }
      }).toPass();
      await testInfo.attach(`parameter-sheet-${width}x${height}`, {
        body: await page.screenshot({
          fullPage: true,
          path: testInfo.outputPath(`parameter-sheet-${width}x${height}.png`),
        }),
        contentType: "image/png",
      });
    }
    passed = true;
  } finally {
    await cleanDeviceFixture(fixture, root, passed, testInfo);
  }
});
