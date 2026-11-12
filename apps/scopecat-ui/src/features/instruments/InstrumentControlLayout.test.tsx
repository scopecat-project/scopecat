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
  return render(
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
}
function resize(value: number) {
  act(() => {
    width = value;
    measure();
    flushFrames();
  });
}

it("starts control-first and retains mounted views, acquisition and result inputs across independent collapse", () => {
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
      name: "控制",
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
  fireEvent.click(screen.getByRole("button", { name: "控制" }));
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
    `${Math.max(320, window.innerHeight - 160 - 20)}px`,
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
