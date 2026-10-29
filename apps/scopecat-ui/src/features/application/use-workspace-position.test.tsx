// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useWorkspacePosition } from "./use-workspace-position";

let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;
beforeEach(() => {
  frames = new Map();
  nextFrame = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockImplementation(function (
    this: HTMLElement,
  ) {
    return { length: this.closest("[hidden]") ? 0 : 1 } as DOMRectList;
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function flushFrames() {
  const pending = [...frames.values()];
  frames.clear();
  for (const callback of pending) callback(0);
}
function Workspace({ view }: { view: string }) {
  useWorkspacePosition(view);
  return (
    <>
      <button>Navigation</button>
      <main>
        <section hidden={view !== "runs"}>
          <input aria-label="Run notes" defaultValue="retained" />
          <button>Run action</button>
        </section>
        <section hidden={view !== "launch"}>
          <input aria-label="Draft input" />
        </section>
      </main>
    </>
  );
}
function retainedRun() {
  const result = render(<Workspace view="runs" />);
  flushFrames();
  const input = screen.getByRole("textbox", { name: "Run notes" }) as HTMLInputElement;
  input.focus();
  input.setSelectionRange(1, 4);
  input.dispatchEvent(new Event("selectionchange", { bubbles: true }));
  result.rerender(<Workspace view="launch" />);
  flushFrames();
  return { ...result, input };
}
it("preserves drawer/history focus acquired after a route schedules position recovery", () => {
  const { rerender } = retainedRun();
  rerender(<Workspace view="runs" />);
  const navigation = screen.getByRole("button", { name: "Navigation" });
  navigation.focus();
  flushFrames();
  expect(navigation).toHaveFocus();
});
it("preserves a new user focus choice made before the recovery frame", () => {
  const { rerender } = retainedRun();
  rerender(<Workspace view="runs" />);
  const action = screen.getByRole("button", { name: "Run action" });
  action.focus();
  flushFrames();
  expect(action).toHaveFocus();
});
it("restores retained input and selection after ordinary navigation", () => {
  const { rerender, input } = retainedRun();
  screen.getByRole("button", { name: "Navigation" }).focus();
  rerender(<Workspace view="runs" />);
  flushFrames();
  expect(input).toHaveFocus();
  expect([input.selectionStart, input.selectionEnd]).toEqual([1, 4]);
});
it("does not move focus or scroll in a hidden document", () => {
  const { rerender } = retainedRun();
  const navigation = screen.getByRole("button", { name: "Navigation" });
  navigation.focus();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  vi.mocked(window.scrollTo).mockClear();
  rerender(<Workspace view="runs" />);
  flushFrames();
  expect(navigation).toHaveFocus();
  expect(window.scrollTo).not.toHaveBeenCalled();
});
it("cancels obsolete restoration when navigation changes again before the frame", () => {
  const { rerender } = retainedRun();
  rerender(<Workspace view="runs" />);
  rerender(<Workspace view="launch" />);
  const input = screen.getByRole("textbox", { name: "Draft input" });
  input.focus();
  flushFrames();
  expect(input).toHaveFocus();
});
