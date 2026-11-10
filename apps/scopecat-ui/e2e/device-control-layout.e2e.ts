import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cleanDeviceFixture, startDeviceFixture } from "./device-fixture";
import { expect, test, type Locator } from "@playwright/test";
import type { components } from "../src/api-schema";

type DeviceView = components["schemas"]["DeviceView"];
const deviceName = "Synthetic VNA " + "long-device-label-".repeat(5);

async function expectFittedCurve(chart: Locator) {
  await expect(chart.locator("svg")).toBeVisible();
  // ResizeObserver can resize the SVG before ECharts positions every label.
  // Retry the complete geometry contract, not only the outer SVG width.
  await expect(async () => {
    const layout = await chart.evaluate((element) => {
      const svg = element.querySelector("svg")!;
      const bounds = svg.getBoundingClientRect();
      return {
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
    expect(layout.labels.some((label) => label.text?.includes("frequency"))).toBe(true);
    expect(layout.labels.some((label) => label.text?.includes("s_parameter"))).toBe(true);
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

test("control inputs and headings stay readable and complete curves resize at every entry", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
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
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto(controlUrl);
    await page.getByRole("button", { name: "连接", exact: true }).click();
    await expect(page.getByText("交互会话已连接", { exact: true })).toBeVisible();
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
    // Keep the same region and curve locatable while deliberately hidden below.
    const current = page.getByRole("region", {
      name: "当前测量结果",
      exact: true,
      includeHidden: true,
    });
    const curve = current.getByRole("img", {
      name: "s_parameter 完整曲线",
      includeHidden: true,
    });
    for (const width of [390, 1280, 390, 760, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await expectFittedCurve(curve);
      expect(
        await page.evaluate<number>("document.documentElement.scrollWidth"),
      ).toBeLessThanOrEqual(width);
    }
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
      body: await page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
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
        await expectFittedCurve(curve);
      }
    }
    await page.setViewportSize({ width: 390, height: 900 });
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    const saveDialog = page.getByRole("dialog", { name: "保存测量", exact: true });
    for (const name of ["标题", "线路名称", "备注"]) {
      const input = saveDialog.getByRole("textbox", { name, exact: true });
      await expect(input).toHaveValue("");
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
    await saveDialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(saveDialog).toBeHidden();
    await read.click();
    await expectFittedCurve(curve);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expectFittedCurve(curve);
      await expectFittedCurve(curve);
      await testInfo.attach(`device-layout-${width}`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    }
    await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
    await saveDialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(saveDialog).toBeHidden();
    const dataHref = await page
      .getByRole("link", { name: "查看 Data", exact: true })
      .getAttribute("href");
    expect(dataHref).toBeTruthy();
    await page.getByRole("button", { name: "释放控制", exact: true }).click();
    await page.getByRole("button", { name: "保持当前状态并结束", exact: true }).click();
    await expect(page.getByRole("button", { name: "连接", exact: true })).toBeEnabled();
    await page.goto(`${url}${dataHref}`);
    const savedCurve = page.getByRole("img", { name: "s_parameter 完整曲线" });
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
