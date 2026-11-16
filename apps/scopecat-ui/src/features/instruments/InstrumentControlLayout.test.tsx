// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InstrumentControlLayout } from "./InstrumentControlLayout";

let width = 1200;
let measure: () => void;
const disconnect = vi.fn();
const mounted = vi.fn();
let frameId = 0;
const frames = new Map<number, FrameRequestCallback>();
function flushFrames() {
  const pending = [...frames.values()];
  frames.clear();
  for (const callback of pending) callback(0);
}
beforeEach(() => {
  width = 1200;
  frameId = 0;
  frames.clear();
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((callback: FrameRequestCallback) => {
      frames.set(++frameId, callback);
      return frameId;
    }),
  );
  vi.stubGlobal(
    "cancelAnimationFrame",
    vi.fn((id: number) => frames.delete(id)),
  );
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({
    width,
    height: 600,
    top: 160,
    left: 0,
    right: width,
    bottom: 760,
    x: 0,
    y: 160,
    toJSON: () => ({}),
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        measure = callback;
      }
      observe() {}
      disconnect = disconnect;
    },
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function StatefulContent({ name }: { name: string }) {
  const [value, setValue] = useState("");
  useEffect(() => {
    mounted(name);
  }, [name]);
  return (
    <label>
      {name}
      <input value={value} onChange={(event) => setValue(event.target.value)} />
    </label>
  );
}
function show(hasResults = true) {
  const rendered = render(
    <InstrumentControlLayout
      hasResults={hasResults}
      pendingCount={2}
      controls={<StatefulContent name="控制草稿" />}
      configuration={<StatefulContent name="配置草稿" />}
      code={<StatefulContent name="代码选项" />}
      acquisition={<button>采集一次</button>}
      results={<StatefulContent name="结果选项" />}
    />,
  );
  // Most regression cases start from the user-selected control-only arrangement.
  if (hasResults) fireEvent.click(screen.getByRole("button", { name: "收起结果" }));
  return rendered;
}
function resize(value: number) {
  act(() => {
    width = value;
    measure();
    flushFrames();
  });
}

it("retains mounted views from a user-selected control-only arrangement, acquisition and result inputs across independent collapse", () => {
  show();
  const draft = screen.getByRole("textbox", { name: "控制草稿" });
  const result = screen.getByRole("textbox", { name: "结果选项", hidden: true });
  expect(result).not.toBeVisible();
  fireEvent.change(draft, { target: { value: "not sent" } });
  fireEvent.change(result, { target: { value: "original result" } });
  fireEvent.click(screen.getByRole("button", { name: "连接配置" }));
  expect(draft).not.toBeVisible();
  expect(screen.getByRole("button", { name: "采集一次" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Python 示例" }));
  fireEvent.click(
    within(screen.getByRole("navigation", { name: "控制工作区页面" })).getByRole("button", {
      name: "参数",
    }),
  );
  expect(screen.getByRole("textbox", { name: "控制草稿" })).toBe(draft);
  expect(draft).toHaveValue("not sent");
  fireEvent.click(screen.getByRole("button", { name: "展开结果" }));
  expect(result).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "收起控制" }));
  expect(draft).not.toBeVisible();
  expect(result).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "收起结果" }));
  expect(draft).toBeVisible();
  expect(result).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "展开结果" }));
  expect(result).toHaveValue("original result");
  expect(mounted.mock.calls.map(([name]) => name)).toEqual([
    "控制草稿",
    "配置草稿",
    "代码选项",
    "结果选项",
  ]);
});

it("bounds keyboard resizing and remembers proportions and wide visibility through compact views", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "展开结果" }));
  const divider = screen.getByRole("separator");
  fireEvent.keyDown(divider, { key: "ArrowRight", shiftKey: true });
  expect(divider).toHaveAttribute("aria-valuenow", "53");
  fireEvent.click(screen.getByRole("button", { name: "收起结果" }));
  fireEvent.click(screen.getByRole("button", { name: "展开结果" }));
  expect(divider).toHaveAttribute("aria-valuenow", "53");
  resize(560);
  expect(divider).not.toBeVisible();
  const switcher = screen.getByRole("group", { name: "显示区域" });
  fireEvent.click(within(switcher).getByRole("button", { name: "控制" }));
  expect(screen.getByRole("textbox", { name: "控制草稿" })).toBeVisible();
  fireEvent.click(within(switcher).getByRole("button", { name: "结果" }));
  expect(screen.getByRole("textbox", { name: "结果选项" })).toBeVisible();
  resize(780); // Hysteresis prevents flickering near the threshold.
  expect(divider).not.toBeVisible();
  resize(1200);
  expect(divider).toBeVisible();
  expect(divider).toHaveAttribute("aria-valuenow", "53");
  fireEvent.keyDown(divider, { key: "Home" });
  expect(divider).toHaveAttribute("aria-valuenow", "30");
  fireEvent.keyDown(divider, { key: "ArrowLeft" });
  expect(divider).toHaveAttribute("aria-valuenow", "30");
  fireEvent.keyDown(divider, { key: "End" });
  expect(divider).toHaveAttribute("aria-valuenow", "70");
  fireEvent.doubleClick(divider);
  expect(divider).toHaveAttribute("aria-valuenow", "48");
});

it("retains independent scroll offsets while hidden views cannot replace them", () => {
  show();
  const controls = screen.getByRole("region", { name: "设备控制内容" });
  Object.defineProperty(controls, "clientHeight", { configurable: true, value: 300 });
  controls.scrollTop = 135;
  fireEvent.scroll(controls);
  fireEvent.click(screen.getByRole("button", { name: "Python 示例" }));
  controls.scrollTop = 0;
  fireEvent.scroll(controls);
  fireEvent.click(screen.getByRole("button", { name: "参数" }));
  expect(controls.scrollTop).toBe(135);
});

it("cancels a pointer drag and releases its capture when the divider disappears", () => {
  vi.stubGlobal(
    "PointerEvent",
    class extends MouseEvent {
      pointerId: number;
      constructor(type: string, init: PointerEventInit) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
      }
    },
  );
  const rendered = show();
  fireEvent.click(screen.getByRole("button", { name: "展开结果" }));
  const divider = screen.getByRole("separator");
  divider.setPointerCapture = vi.fn();
  divider.hasPointerCapture = vi.fn(() => true);
  const releaseCapture = vi.fn();
  divider.releasePointerCapture = releaseCapture;
  fireEvent.pointerDown(divider, { pointerId: 2, button: 0 });
  fireEvent.pointerMove(divider, { pointerId: 2, clientX: 720 });
  const moved = divider.getAttribute("aria-valuenow");
  expect(moved).toBe("60");
  expect(divider.parentElement!.style.getPropertyValue("--device-divider-position")).toBe("60%");
  fireEvent.pointerDown(divider, { pointerId: 9, button: 0 });
  fireEvent.pointerMove(divider, { pointerId: 9, clientX: 900 });
  fireEvent.pointerUp(divider, { pointerId: 9 });
  expect(divider).toHaveAttribute("aria-valuenow", moved);
  expect(divider).toHaveClass("is-dragging");
  fireEvent.pointerCancel(divider, { pointerId: 9 });
  fireEvent.lostPointerCapture(divider, { pointerId: 9 });
  fireEvent.pointerMove(divider, { pointerId: 2, clientX: 800 });
  const primaryMoved = divider.getAttribute("aria-valuenow");
  expect(primaryMoved).not.toBe(moved);
  expect(divider).toHaveClass("is-dragging");
  expect(releaseCapture).not.toHaveBeenCalled();
  fireEvent.pointerCancel(divider, { pointerId: 2 });
  fireEvent.pointerMove(divider, { pointerId: 2, clientX: 900 });
  expect(divider).toHaveAttribute("aria-valuenow", primaryMoved);
  expect(divider).not.toHaveClass("is-dragging");
  fireEvent.pointerDown(divider, { pointerId: 3, button: 0 });
  resize(500);
  expect(releaseCapture).toHaveBeenCalledWith(3);
  resize(1200);
  fireEvent.pointerDown(divider, { pointerId: 4, button: 0 });
  rendered.unmount();
  expect(releaseCapture).toHaveBeenCalledWith(4);
  expect(disconnect).toHaveBeenCalledOnce();
});

it("does not reserve result controls for a device without acquisitions or retained results", () => {
  show(false);
  expect(screen.queryByRole("group", { name: "显示区域" })).not.toBeInTheDocument();
  expect(screen.queryByRole("separator")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "采集一次" })).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "控制草稿" })).toBeVisible();
  resize(390);
  expect(screen.queryByRole("group", { name: "显示区域" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "采集结果工作区" })).not.toBeInTheDocument();
  expect(screen.queryByRole("separator")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "控制草稿" })).toBeVisible();
});

it("keeps a focused control visible when entering compact mode and gives a hidden divider a visible target", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "展开结果" }));
  const input = screen.getByRole("textbox", { name: "控制草稿" });
  act(() => input.focus());
  resize(560);
  expect(input).toBeVisible();
  expect(input).toHaveFocus();
  resize(1200);
  const divider = screen.getByRole("separator");
  act(() => divider.focus());
  resize(560);
  expect(divider).not.toBeVisible();
  expect(
    within(screen.getByRole("group", { name: "显示区域" })).getByRole("button", { name: "控制" }),
  ).toHaveFocus();
});

it("preserves wide visibility and restores focus to the results entry when its compact input becomes hidden", () => {
  show();
  resize(560);
  fireEvent.click(
    within(screen.getByRole("group", { name: "显示区域" })).getByRole("button", { name: "结果" }),
  );
  const result = screen.getByRole("textbox", { name: "结果选项" });
  act(() => result.focus());
  resize(1200);
  expect(result).not.toBeVisible();
  expect(screen.getByRole("button", { name: "展开结果" })).toHaveFocus();
  expect(screen.queryByRole("separator")).not.toBeInTheDocument();
});

it("measures synchronously at mount and coalesces observer notifications into one frame", () => {
  show();
  const layout = screen.getByTestId("instrument-workbench-layout");
  expect(layout.style.getPropertyValue("--device-workspace-height")).toBe(
    `${Math.max(320, window.innerHeight - 160)}px`,
  );
  expect(requestAnimationFrame).not.toHaveBeenCalled();
  const bounds = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
  bounds.mockClear();
  act(() => {
    width = 560;
    measure();
    measure();
    measure();
  });
  expect(bounds).not.toHaveBeenCalled();
  expect(requestAnimationFrame).toHaveBeenCalledOnce();
  expect(layout).toHaveAttribute("data-compact", "false");
  act(flushFrames);
  expect(bounds).toHaveBeenCalledOnce();
  expect(layout).toHaveAttribute("data-compact", "true");
  resize(1200);
  expect(requestAnimationFrame).toHaveBeenCalledTimes(2);
});

it("cancels a pending measurement on unmount", () => {
  const rendered = show();
  act(() => {
    measure();
    measure();
  });
  const bounds = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
  bounds.mockClear();
  rendered.unmount();
  expect(disconnect).toHaveBeenCalledOnce();
  expect(cancelAnimationFrame).toHaveBeenCalledWith(1);
  expect(frames.size).toBe(0);
  act(flushFrames);
  expect(bounds).not.toHaveBeenCalled();
});

it("retains its last usable measurement while hidden and measures again when revealed", () => {
  show();
  const layout = screen.getByTestId("instrument-workbench-layout");
  const height = layout.style.getPropertyValue("--device-workspace-height");
  resize(0);
  expect(layout).toHaveAttribute("data-compact", "false");
  expect(layout.style.getPropertyValue("--device-workspace-height")).toBe(height);
  resize(560);
  expect(layout).toHaveAttribute("data-compact", "true");
  resize(0);
  expect(layout).toHaveAttribute("data-compact", "true");
  resize(1200);
  expect(layout).toHaveAttribute("data-compact", "false");
});

it("keeps one visible pane switch and keyboard focus when controls collapse", () => {
  show();
  const button = screen.getByRole("button", { name: "收起控制" });
  button.focus();
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: "展开控制" })).toBe(button);
  expect(button).toHaveFocus();
  expect(button).toBeVisible();
  expect(screen.getAllByRole("group", { name: "显示区域", hidden: true })).toHaveLength(1);
  expect(screen.getByRole("region", { name: "采集结果工作区" })).not.toContainElement(button);
});

it("keeps the control page active when its navigation was focused before narrowing", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "展开结果" }));
  act(() => screen.getByRole("textbox", { name: "结果选项" }).focus());
  const configuration = screen.getByRole("button", { name: "连接配置" });
  act(() => configuration.focus());
  fireEvent.click(configuration);
  resize(560);
  expect(screen.getByRole("textbox", { name: "配置草稿" })).toBeVisible();
  expect(screen.getByRole("textbox", { name: "结果选项", hidden: true })).not.toBeVisible();
});

it("keeps parameter actions outside the scrolling form and hides them on other tasks", () => {
  render(
    <InstrumentControlLayout
      hasResults
      pendingCount={0}
      controls={<input aria-label="参数" />}
      parameterActions={<button>写入设备</button>}
      acquisition={<button>采集</button>}
      results={<div>曲线</div>}
      code={<pre>example</pre>}
    />,
  );
  const form = screen.getByRole("region", { name: "设备控制内容" });
  const write = screen.getByRole("button", { name: "写入设备" });
  expect(form).not.toContainElement(write);
  expect(screen.getByRole("region", { name: "控制工作区" })).toContainElement(write);
  expect(screen.getByRole("navigation", { name: "控制工作区页面" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Python 示例" }));
  expect(write).not.toBeVisible();
  expect(screen.getByRole("button", { name: "采集" })).toBeVisible();
});

it("opens capable devices with both workspaces and no data requirement", () => {
  render(
    <InstrumentControlLayout
      controls={<div>参数内容</div>}
      acquisition={<button>采集</button>}
      results={<div>尚无结果</div>}
      code={<div>代码</div>}
      hasResults
      pendingCount={0}
    />,
  );
  expect(screen.getByText("尚无结果")).toBeVisible();
  expect(screen.getByText("参数内容")).toBeVisible();
  expect(screen.getByRole("separator")).toBeVisible();
});

it("keeps one shared navigation surface and restores a control task directly from results", () => {
  show();
  resize(560);
  const pages = screen.getByRole("navigation", { name: "控制工作区页面" });
  const regions = screen.getByRole("group", { name: "显示区域" });
  expect(pages.parentElement).toBe(regions.parentElement);
  fireEvent.click(within(regions).getByRole("button", { name: "结果" }));
  expect(pages).toBeVisible();
  expect(screen.getByRole("button", { name: "参数" })).toHaveAttribute("aria-pressed", "false");
  const code = screen.getByRole("button", { name: "Python 示例" });
  code.focus();
  fireEvent.click(code);
  expect(code).toHaveFocus();
  expect(code).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("textbox", { name: "代码选项" })).toBeVisible();
  expect(screen.getByRole("textbox", { name: "结果选项", hidden: true })).not.toBeVisible();
  resize(1200);
  expect(screen.getByRole("navigation", { name: "控制工作区页面" })).toBe(pages);
  expect(code).toHaveFocus();
});

it("retains the wide task rail when controls are hidden and opens the chosen task", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "收起控制" }));
  const pages = screen.getByRole("navigation", { name: "控制工作区页面" });
  expect(pages).toBeVisible();
  fireEvent.click(within(pages).getByRole("button", { name: "连接配置" }));
  expect(screen.getByRole("textbox", { name: "配置草稿" })).toBeVisible();
  expect(screen.getByRole("textbox", { name: "结果选项" })).toBeVisible();
});

it("uses the same task navigation without empty region controls for a source-only device", () => {
  show(false);
  resize(560);
  expect(screen.queryByRole("group", { name: "显示区域" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Python 示例" }));
  expect(screen.getByRole("textbox", { name: "代码选项" })).toBeVisible();
  resize(1200);
  expect(screen.getByRole("textbox", { name: "代码选项" })).toBeVisible();
  expect(screen.queryByRole("separator")).not.toBeInTheDocument();
});
