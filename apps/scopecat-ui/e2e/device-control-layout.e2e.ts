import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cleanDeviceFixture, startDeviceFixture } from "./device-fixture";
import { expect, test, type Locator, type Page } from "@playwright/test";
import type { components } from "../src/api-schema";

type DeviceView = components["schemas"]["DeviceView"];
const longAxisLabel = "s_parameter · measured transmission (complex)";
const deviceName = "Synthetic VNA " + "long-device-label-".repeat(5);

async function showResults(page: Page) {
  const layout = page.getByTestId("instrument-workbench-layout");
  await expect
    .poll(() =>
      layout.evaluate((element) => {
        const width = element.getBoundingClientRect().width;
        const compact = element.getAttribute("data-compact") === "true";
        return width < 760 ? compact : width >= 800 ? !compact : true;
      }),
    )
    .toBe(true);
  if ((await layout.getAttribute("data-compact")) === "true")
    await page
      .getByRole("group", { name: "显示区域" })
      .getByRole("button", { name: "结果", exact: true })
      .click();
  else if ((await layout.getAttribute("data-collection-collapsed")) === "true")
    await page.getByRole("button", { name: "展开结果", exact: true }).click();
}
async function showControls(page: Page) {
  const layout = page.getByTestId("instrument-workbench-layout");
  await expect
    .poll(() =>
      layout.evaluate((element) => {
        const width = element.getBoundingClientRect().width;
        const compact = element.getAttribute("data-compact") === "true";
        return width < 760 ? compact : width >= 800 ? !compact : true;
      }),
    )
    .toBe(true);
  if ((await layout.getAttribute("data-compact")) === "true")
    await page
      .getByRole("group", { name: "显示区域" })
      .getByRole("button", { name: "控制", exact: true })
      .click();
  else if ((await layout.getAttribute("data-control-collapsed")) === "true")
    await page.getByRole("button", { name: "展开控制", exact: true }).click();
}

async function expectFittedCurve(chart: Locator) {
  await expect(chart.locator("svg")).toBeVisible();
  // ResizeObserver can resize the SVG before ECharts positions every label.
  // Retry the complete geometry contract, not only the outer SVG width.
  await expect(async () => {
    const layout = await chart.evaluate((element) => {
      const svg = element.querySelector("svg")!;
      const bounds = svg.getBoundingClientRect();
      // The first clip belongs to the line series, not the dataZoom overview.
      const plot = (
        svg.querySelector("clipPath path") as unknown as {
          getBBox(): { width: number; height: number };
        }
      ).getBBox();
      return {
        plot: { width: plot.width, height: plot.height },
        caption: element.previousElementSibling!.textContent,
        xTicks: [...svg.querySelectorAll('text[text-anchor="middle"]')]
          .map((label) => label.textContent ?? "")
          .filter((text) => /^-?[\d,.]+(?:e[+-]?\d+)?$/.test(text)),
        width: bounds.width,
        containerWidth: element.clientWidth,
        left: bounds.left,
        right: bounds.right,
        viewport: element.ownerDocument.defaultView!.innerWidth,
        overflow: element.scrollWidth - element.clientWidth,
        labels: [...svg.querySelectorAll("text")].map((label) => {
          const rect = label.getBoundingClientRect();
          return {
            text: label.textContent,
            left: rect.left - bounds.left,
            right: rect.right - bounds.left,
            top: rect.top - bounds.top,
            bottom: rect.bottom - bounds.top,
          };
        }),
      };
    });
    expect(Math.abs(layout.width - layout.containerWidth)).toBeLessThanOrEqual(1);
    expect(layout.width).toBeGreaterThan(200);
    expect(layout.left).toBeGreaterThanOrEqual(0);
    expect(layout.right).toBeLessThanOrEqual(layout.viewport);
    expect(layout.overflow).toBeLessThanOrEqual(1);
    expect(layout.caption).toContain("横轴 frequency (Hz)");
    expect(layout.caption).toContain("纵轴 s_parameter");
    expect(layout.plot.width).toBeGreaterThanOrEqual(140);
    expect(layout.plot.height).toBeGreaterThanOrEqual(100);
    expect(new Set(layout.xTicks).size).toBeGreaterThanOrEqual(2);
    if (layout.width <= 420) {
      expect(layout.plot.width / layout.width).toBeGreaterThanOrEqual(0.5);
      expect(layout.labels.some((label) => label.text?.includes("frequency"))).toBe(false);
      expect(layout.xTicks.some((tick) => tick.includes("e+"))).toBe(true);
    } else {
      expect(layout.labels.some((label) => label.text?.includes("frequency"))).toBe(true);
      // Bounded previews keep scientific tick labels at every width; these are
      // display-only and retain the exact values rather than restoring comma labels.
      expect(layout.xTicks.some((tick) => tick.includes("e+"))).toBe(true);
    }
    for (const label of layout.labels) {
      expect(label.left, JSON.stringify(label)).toBeGreaterThanOrEqual(-1);
      expect(label.right, JSON.stringify(label)).toBeLessThanOrEqual(layout.width + 1);
      expect(label.top, JSON.stringify(label)).toBeGreaterThanOrEqual(-1);
      expect(label.bottom, JSON.stringify(label)).toBeLessThanOrEqual(261);
    }
    // Containment alone permits unreadable GHz tick labels to overlap in narrow panels.
    for (const [index, label] of layout.labels.entries()) {
      for (const next of layout.labels.slice(index + 1)) {
        const overlapX = Math.min(label.right, next.right) - Math.max(label.left, next.left);
        const overlapY = Math.min(label.bottom, next.bottom) - Math.max(label.top, next.top);
        expect(overlapX > 1 && overlapY > 1, JSON.stringify([label, next])).toBe(false);
      }
    }
  }).toPass({ timeout: 5_000, intervals: [100, 250, 500] });
}

test("control inputs and headings stay readable and bounded curves resize at every entry", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  await page.addInitScript(`
    window.controlLayoutErrors = [];
    window.addEventListener("error", (event) => window.controlLayoutErrors.push(event.message));
  `);
  const root = await mkdtemp(join(tmpdir(), "scopecat-curve-layout-"));
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
    // Exercise a long display label without changing device data or backend save identity.
    await page.route("**/api/v1/instrument-sessions", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const response = await route.fetch();
      const session: components["schemas"]["InstrumentSessionOpenReceipt"] = await response.json();
      for (const description of session.descriptions)
        for (const contract of description.interfaces ?? [])
          for (const acquisition of contract.acquisitions ?? [])
            for (const result of acquisition.results)
              if (result.id === "s_parameter") result.label = longAxisLabel;
      await route.fulfill({ response, json: session });
    });
    // The review summary now carries the authoritative acquisition contract.
    // Modify only its presentation label, never retained bytes or save identity.
    await page.route(
      "**/api/v1/instrument-sessions/*/instruments/*/collect-review",
      async (route) => {
        const response = await route.fetch();
        const summary: components["schemas"]["CollectReviewReceipt"] = await response.json();
        for (const contract of summary.pending?.description.interfaces ?? [])
          for (const acquisition of contract.acquisitions ?? [])
            for (const result of acquisition.results)
              if (result.id === "s_parameter") result.label = longAxisLabel;
        await route.fulfill({ response, json: summary });
      },
    );
    await page.getByRole("button", { name: "连接", exact: true }).click();
    await expect(page.getByText("交互会话已连接", { exact: true })).toBeVisible();
    await page.screenshot({
      path: "../../.test-results/layout-c-control-1280.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(page.getByRole("heading", { name: deviceName, exact: true })).toHaveCount(1);
    const header = page
      .getByRole("heading", { name: deviceName, level: 1 })
      .locator("..")
      .locator("..")
      .locator("..");
    await expect(header.getByText("已连接", { exact: true })).toBeVisible();
    await expect(header.getByRole("button", { name: "刷新占用" })).toBeVisible();
    const sweep = page.getByTitle("scopecat.network_sweep/v1 / ", { exact: true });
    const read = page.getByTitle("scopecat.existing_network_trace/v1 / ", { exact: true });
    await sweep.click();
    await showResults(page);
    await expect(page.getByRole("button", { name: "保存所选结果", exact: true })).toBeInViewport();
    await expect(
      page.getByRole("combobox", { name: "选择近期结果", exact: true }),
    ).toBeInViewport();
    // Keep the same region and curve locatable while deliberately hidden below.
    const current = page.getByRole("region", {
      name: "当前测量结果",
      exact: true,
      includeHidden: true,
    });
    const curve = current.getByRole("img", {
      name: "s_parameter 局部曲线",
      includeHidden: true,
    });
    for (const width of [390, 1280, 390, 760, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await showResults(page);
      await expectFittedCurve(curve);
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
    }
    // Native regression: around 893px, a 59/41 split leaves a roughly 300px results pane.
    // Contained SVG labels alone used to pass while the actual plot shrank to about 57px.
    await page.setViewportSize({ width: 893, height: 700 });
    await showResults(page);
    await showControls(page);
    const narrowDivider = page.getByRole("separator", { name: "调整控制与结果区域大小" });
    await narrowDivider.dblclick();
    await narrowDivider.focus();
    await page.keyboard.press("Shift+ArrowRight");
    for (let step = 0; step < 3; step++) await page.keyboard.press("ArrowRight");
    await expect(narrowDivider).toHaveAttribute("aria-valuenow", "59");
    const narrowResults = page.getByRole("region", { name: "采集结果工作区", exact: true });
    const narrowWidth = (await narrowResults.boundingBox())!.width;
    expect(narrowWidth).toBeGreaterThanOrEqual(300);
    expect(narrowWidth).toBeLessThanOrEqual(350);
    await expect(current.getByText(/^纵轴 /)).toContainText(longAxisLabel);
    for (const value of ["phase", "real", "imag", "magnitude"]) {
      await current.getByRole("combobox", { name: "复数表示" }).selectOption(value);
      await expectFittedCurve(curve);
    }
    await curve.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "../../.test-results/layout-c-chart-893.png", fullPage: true });
    // Preserve this original result before adding the high-offset sample. The final
    // session-close check must still require every pending result to be accounted for.
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    const originalSave = page.getByRole("dialog", { name: "保存测量", exact: true });
    await originalSave.getByRole("button", { name: "保存", exact: true }).click();
    await expect(originalSave).toBeHidden();
    // A 1 MHz span around 5 GHz must not round distinct narrow ticks to "5.00e+9".
    await page
      .getByTestId("interface-property-start_frequency")
      .getByRole("spinbutton")
      .fill("5000000000");
    await page
      .getByTestId("interface-property-stop_frequency")
      .getByRole("spinbutton")
      .fill("5001000000");
    const apply = page.getByRole("button", { name: "应用暂存修改", exact: true });
    await apply.click();
    await expect(apply).toBeDisabled();
    await sweep.click();
    await expect(page.getByTestId(/^recent-result-/)).toHaveCount(2);
    await expectFittedCurve(curve);
    const closeTicks = await curve.locator('svg text[text-anchor="middle"]').allTextContents();
    const closeValues = closeTicks
      .filter((text) => /^-?[\d,.]+(?:e[+-]?\d+)?$/.test(text))
      .map((text) => Number(text.replaceAll(",", "")));
    expect(new Set(closeValues).size).toBeGreaterThanOrEqual(2);
    expect(Math.min(...closeValues)).toBeGreaterThanOrEqual(5e9);
    expect(Math.max(...closeValues)).toBeLessThanOrEqual(5.001e9);
    await curve.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: "../../.test-results/layout-c-chart-offset-893.png",
      fullPage: true,
    });
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    const narrowSave = page.getByRole("dialog", { name: "保存测量", exact: true });
    await narrowSave.getByRole("textbox", { name: "标题", exact: true }).fill("Narrow plot draft");
    await page.setViewportSize({ width: 800, height: 600 });
    await expect(page.getByTestId("instrument-workbench-layout")).toHaveAttribute(
      "data-compact",
      "true",
    );
    await expect(narrowSave).toBeVisible();
    await expect(narrowSave.getByRole("textbox", { name: "标题", exact: true })).toHaveValue(
      "Narrow plot draft",
    );
    await page.screenshot({
      path: "../../.test-results/layout-c-chart-save-800.png",
      fullPage: true,
    });
    await narrowSave.getByRole("button", { name: "取消", exact: true }).click();
    await showResults(page);
    await narrowResults.evaluate((element) => {
      element.scrollTop = 0;
    });
    await expectFittedCurve(curve);
    await curve.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "../../.test-results/layout-c-chart-800.png", fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    await showResults(page);
    await narrowDivider.dblclick();
    // A 1920px viewport still leaves only a narrow settings column. Fields
    // must adapt to that container rather than using viewport-wide two columns.
    await page.setViewportSize({ width: 1920, height: 1080 });
    for (const id of ["start_frequency", "stop_frequency"]) {
      const row = page.getByTestId(`interface-property-${id}`);
      const label = row.getByTestId("property-label");
      const input = row.getByRole("spinbutton");
      await expect(label).toBeVisible();
      await expect(input).toBeVisible();
      await expect(async () => {
        const geometry = await row.evaluate((element) => {
          const labelNode = element.querySelector('[data-testid="property-label"]')!;
          const inputNode = element.querySelector('input[type="number"]')!;
          // This helper must travel with the browser-evaluated callback.
          // oxlint-disable-next-line unicorn/consistent-function-scoping
          const rect = (node: {
            getBoundingClientRect(): { left: number; right: number; top: number; bottom: number };
          }) => {
            const bounds = node.getBoundingClientRect();
            return {
              left: bounds.left,
              right: bounds.right,
              top: bounds.top,
              bottom: bounds.bottom,
            };
          };
          return {
            row: rect(element),
            container: rect(element.parentElement),
            card: rect(element.closest("article")),
            label: rect(labelNode),
            input: rect(inputNode),
            labelWidth: labelNode.clientWidth,
            labelContentWidth: labelNode.scrollWidth,
          };
        });
        expect(geometry.labelContentWidth).toBeLessThanOrEqual(geometry.labelWidth + 1);
        for (const bounds of [geometry.label, geometry.input]) {
          for (const container of [geometry.row, geometry.container, geometry.card]) {
            expect(bounds.left).toBeGreaterThanOrEqual(container.left - 1);
            expect(bounds.right).toBeLessThanOrEqual(container.right + 1);
            expect(bounds.top).toBeGreaterThanOrEqual(container.top - 1);
            expect(bounds.bottom).toBeLessThanOrEqual(container.bottom + 1);
          }
        }
        const overlapX =
          Math.min(geometry.label.right, geometry.input.right) -
          Math.max(geometry.label.left, geometry.input.left);
        const overlapY =
          Math.min(geometry.label.bottom, geometry.input.bottom) -
          Math.max(geometry.label.top, geometry.input.top);
        expect(overlapX > 1 && overlapY > 1).toBe(false);
      }).toPass({ timeout: 5_000 });
    }
    const startInput = await page
      .getByTestId("interface-property-start_frequency")
      .getByRole("spinbutton")
      .boundingBox();
    const stopInput = await page
      .getByTestId("interface-property-stop_frequency")
      .getByRole("spinbutton")
      .boundingBox();
    expect(startInput).not.toBeNull();
    expect(stopInput).not.toBeNull();
    const inputsOverlapX =
      Math.min(startInput!.x + startInput!.width, stopInput!.x + stopInput!.width) -
      Math.max(startInput!.x, stopInput!.x);
    const inputsOverlapY =
      Math.min(startInput!.y + startInput!.height, stopInput!.y + stopInput!.height) -
      Math.max(startInput!.y, stopInput!.y);
    expect(inputsOverlapX > 1 && inputsOverlapY > 1).toBe(false);
    await testInfo.attach("device-control-fields-1920", {
      body: await page.screenshot({
        path: "../../.test-results/layout-c-split-1920.png",
        fullPage: true,
      }),
      contentType: "image/png",
    });
    // Actual pane geometry follows both keyboard and pointer resizing, including a very wide window.
    const divider = page.getByRole("separator", { name: "调整控制与结果区域大小" });
    const controlsPane = page.getByRole("region", { name: "控制工作区", exact: true });
    for (const width of [1280, 2400]) {
      await page.setViewportSize({ width, height: 900 });
      await showResults(page);
      await showControls(page);
      for (const key of ["Home", "End"]) {
        await divider.focus();
        await page.keyboard.press(key);
        const value = Number(await divider.getAttribute("aria-valuenow"));
        expect(value).toBeGreaterThanOrEqual(Number(await divider.getAttribute("aria-valuemin")));
        expect(value).toBeLessThanOrEqual(Number(await divider.getAttribute("aria-valuemax")));
        await expectFittedCurve(curve);
        const pane = await controlsPane.boundingBox();
        const handle = await divider.boundingBox();
        expect(Math.abs(pane!.x + pane!.width - handle!.x)).toBeLessThanOrEqual(2);
      }
      await divider.dblclick();
      await expect(divider).toHaveAttribute("aria-valuenow", "48");
      if (width === 2400)
        await page.screenshot({
          path: "../../.test-results/layout-c-split-2400.png",
          fullPage: true,
        });
    }
    const handle = (await divider.boundingBox())!;
    await page.mouse.move(handle.x + handle.width / 2, handle.y + 50);
    await page.mouse.down();
    await page.mouse.move(handle.x + 130, handle.y + 50);
    await page.mouse.up();
    const rememberedSplit = await divider.getAttribute("aria-valuenow");
    expect(Number(rememberedSplit)).toBeGreaterThan(48);
    await page.getByRole("button", { name: "收起结果", exact: true }).click();
    await page.getByRole("button", { name: "展开结果", exact: true }).click();
    await expect(divider).toHaveAttribute("aria-valuenow", rememberedSplit!);

    // Per-pane scroll, input drafts, curve representation and selected receipt survive view switches.
    await page.setViewportSize({ width: 1280, height: 600 });
    await showResults(page);
    await page.screenshot({ path: "../../.test-results/layout-c-wide-short.png", fullPage: true });
    const controlScroll = page.getByRole("region", { name: "设备控制内容", exact: true });
    const resultScroll = page.getByRole("region", { name: "采集结果工作区", exact: true });
    await controlScroll.evaluate((element) => {
      element.scrollTop = 150;
    });
    await expect.poll(() => controlScroll.evaluate((element) => element.scrollTop)).toBe(150);
    await resultScroll.evaluate((element) => {
      element.scrollTop = 120;
    });
    await expect.poll(() => resultScroll.evaluate((element) => element.scrollTop)).toBe(120);
    await page.getByRole("button", { name: "连接配置", exact: true }).click();
    await expect(page.getByRole("region", { name: "设备连接配置", exact: true })).toBeVisible();
    await expect(resultScroll).toBeVisible();
    await page.getByRole("button", { name: "Python 示例", exact: true }).click();
    await page
      .getByRole("navigation", { name: "控制工作区页面" })
      .getByRole("button", { name: "控制", exact: true })
      .click();
    await expect.poll(() => controlScroll.evaluate((element) => element.scrollTop)).toBe(150);
    await page.getByRole("button", { name: "收起结果", exact: true }).click();
    await page.getByRole("button", { name: "展开结果", exact: true }).click();
    await expect.poll(() => resultScroll.evaluate((element) => element.scrollTop)).toBe(120);
    // The saved pane offset must not scroll frequent actions out of reach. This
    // includes the bottom of the full recent list, beyond the current preview.
    await resultScroll.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    const savedOffset = await resultScroll.evaluate((element) => element.scrollTop);
    expect(savedOffset).toBeGreaterThan(120);
    for (const width of [1280, 390, 1280]) {
      await page.setViewportSize({ width, height: 600 });
      await showResults(page);
      const actions = page.getByRole("group", { name: "所选结果操作" });
      await expect(
        actions.getByRole("button", { name: "保存所选结果", exact: true }),
      ).toBeInViewport();
      await expect(
        actions.getByRole("combobox", { name: "选择近期结果", exact: true }),
      ).toBeInViewport();
      const selector = actions.getByRole("combobox", { name: "选择近期结果", exact: true });
      await selector.focus();
      await expect(selector).toBeFocused();
      const representation = current.getByRole("combobox", { name: "复数表示" });
      // The actual measured sticky height, including wrapped narrow rows, is
      // reserved for keyboard/programmatic scroll targets rather than hiding focus.
      await expect
        .poll(async () => {
          const inset = await resultScroll.evaluate((element) =>
            parseFloat(
              element.ownerDocument.defaultView!.getComputedStyle(element).scrollPaddingTop,
            ),
          );
          return inset >= (await actions.boundingBox())!.height;
        })
        .toBe(true);
      await representation.evaluate((element) => element.scrollIntoView({ block: "start" }));
      await representation.focus();
      await expect(representation).toBeFocused();
      await expect(async () => {
        const bar = (await actions.boundingBox())!;
        const field = (await representation.boundingBox())!;
        expect(field.y).toBeGreaterThanOrEqual(bar.y + bar.height);
      }).toPass();
      await resultScroll.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
    }
    await page.getByRole("button", { name: "收起结果", exact: true }).click();
    await page.getByRole("button", { name: "展开结果", exact: true }).click();
    await expect(page.getByRole("button", { name: "保存所选结果", exact: true })).toBeInViewport();
    await resultScroll.evaluate((element) => {
      element.scrollTop = 120;
    });
    const draft = page.getByTestId("interface-property-start_frequency").getByRole("spinbutton");
    const draftedValue = String(Number(await draft.inputValue()) + 1);
    await draft.fill(draftedValue);
    await current.getByRole("combobox", { name: "复数表示" }).selectOption("phase");
    await draft.focus();
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(draft).toBeVisible();
    await expect(draft).toBeFocused();
    await expect(draft).toHaveValue(draftedValue);
    await page.screenshot({ path: "../../.test-results/layout-c-control-390.png", fullPage: true });
    await showResults(page);
    await expect(current.getByRole("combobox", { name: "复数表示" })).toHaveValue("phase");
    await page.screenshot({ path: "../../.test-results/layout-c-results-390.png", fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    await showResults(page);
    await expect(divider).toHaveAttribute("aria-valuenow", rememberedSplit!);
    await divider.focus();
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(divider).toBeHidden();
    await expect(
      page
        .getByRole("group", { name: "显示区域" })
        .getByRole("button", { name: "结果", exact: true }),
    ).toBeFocused();
    await showControls(page);
    await expect(draft).toHaveValue(draftedValue);
    await page.getByRole("button", { name: "重置", exact: true }).click();
    await showResults(page);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await showResults(page);

    // Resize the containing panel without changing the viewport, then hide/show it.
    await current.evaluate((element) => {
      element.style.width = "360px";
    });
    await expectFittedCurve(curve);
    await current.evaluate((element) => {
      element.style.display = "none";
    });
    await expect(current).toBeHidden();
    await expect(curve).toBeHidden();
    await current.evaluate((element) => {
      element.style.display = "";
      element.style.width = "";
    });
    await expect(current).toBeVisible();
    await expectFittedCurve(curve);
    for (const value of ["phase", "real", "imag", "magnitude"]) {
      await current.getByRole("combobox", { name: "复数表示" }).selectOption(value);
      for (const width of [390, 1280]) {
        await page.setViewportSize({ width, height: 900 });
        await showResults(page);
        await expectFittedCurve(curve);
      }
    }
    // A compact save dialog survives restoring a wide layout whose results are hidden.
    await page.getByRole("button", { name: "收起结果", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 900 });
    await showResults(page);
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    const saveDialog = page.getByRole("dialog", { name: "保存测量", exact: true });
    await saveDialog.getByRole("textbox", { name: "标题", exact: true }).fill("Resize draft");
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(saveDialog).toBeVisible();
    await expect(saveDialog.getByRole("textbox", { name: "标题", exact: true })).toHaveValue(
      "Resize draft",
    );
    await saveDialog.getByRole("button", { name: "取消", exact: true }).click();
    await expect(page.getByRole("button", { name: "展开结果", exact: true })).toBeFocused();
    await page.setViewportSize({ width: 390, height: 900 });
    await showResults(page);
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    for (const name of ["标题", "线路名称", "备注"]) {
      const input = saveDialog.getByRole("textbox", { name, exact: true });
      // The bounded-result display/save store retains drafts after Cancel.
      await expect(input).toHaveValue(name === "标题" ? "Resize draft" : "");
      const appearance = await input.evaluate((element) => {
        const css = element.ownerDocument.defaultView!.getComputedStyle(element);
        const bounds = element.getBoundingClientRect();
        return {
          border: css.borderTopWidth,
          background: css.backgroundColor,
          height: bounds.height,
          right: bounds.right,
        };
      });
      expect(parseFloat(appearance.border)).toBeGreaterThanOrEqual(1);
      expect(appearance.background).not.toBe("rgba(0, 0, 0, 0)");
      expect(appearance.height).toBeGreaterThanOrEqual(36);
      expect(appearance.right).toBeLessThanOrEqual(390);
    }
    const line = saveDialog.getByRole("textbox", { name: "线路名称", exact: true });
    await line.focus();
    await page.keyboard.press("Tab");
    const note = saveDialog.getByRole("textbox", { name: "备注", exact: true });
    await expect(note).toBeFocused();
    expect(
      await note.evaluate(
        (element) => element.ownerDocument.defaultView!.getComputedStyle(element).outlineStyle,
      ),
    ).not.toBe("none");
    await line.fill("layout-line");
    await note.fill("layout-only");
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(saveDialog).toBeVisible();
    await expect(note).toHaveValue("layout-only");
    await expect(note).toBeFocused();
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(saveDialog).toBeVisible();
    await expect(line).toHaveValue("layout-line");
    await page.setViewportSize({ width: 1280, height: 900 });
    await expect(saveDialog).toBeVisible();
    await saveDialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(saveDialog).toBeHidden();
    await expect(page.getByRole("button", { name: "展开结果", exact: true })).toBeFocused();
    await page.setViewportSize({ width: 390, height: 900 });
    await showControls(page);
    await read.click();
    await showResults(page);
    await expectFittedCurve(curve);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await showResults(page);
      await expectFittedCurve(curve);
      await testInfo.attach(`device-layout-${width}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    await saveDialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(saveDialog).toBeHidden();
    const resultHref = await page
      .getByRole("link", { name: "查看已保存结果", exact: true })
      .getAttribute("href");
    expect(resultHref).toBeTruthy();
    await page.getByRole("button", { name: "释放控制", exact: true }).click();
    await page.getByRole("button", { name: "保持当前状态并结束", exact: true }).click();
    await expect(page.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    // Session teardown/rebuild changes the workbench's top and parent height.
    // Wait beyond the closing render to catch deferred ResizeObserver errors.
    const settledLayout = await page.evaluate<{
      errors: string[];
      frames: string[];
    }>(`(async () => {
      for (let frame = 0; frame < 4; frame++)
        await new Promise((resolve) => requestAnimationFrame(resolve));
      const frames = [];
      for (let frame = 0; frame < 4; frame++) {
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const layout = document.querySelector('[data-testid="instrument-workbench-layout"]');
        frames.push(JSON.stringify([layout, layout.parentElement].map((element) => {
          const { width, height, top } = element.getBoundingClientRect();
          return { width, height, top };
        })));
      }
      return { errors: window.controlLayoutErrors, frames };
    })()`);
    expect(settledLayout.errors).toEqual([]);
    expect(new Set(settledLayout.frames).size).toBe(1);
    await page.goto(`${url}${resultHref}`);
    const savedCurve = page.getByRole("img", { name: "s_parameter 局部曲线" });
    for (const width of [390, 1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expectFittedCurve(savedCurve);
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
      await testInfo.attach(`saved-curve-layout-${width}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }
    passed = true;
  } finally {
    await cleanDeviceFixture(fixture, root, passed, testInfo);
  }
});
