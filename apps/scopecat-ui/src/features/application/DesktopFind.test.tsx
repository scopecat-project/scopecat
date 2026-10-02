// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DesktopFind } from "./DesktopFind";

beforeEach(() => {
  vi.stubGlobal("CSS", { highlights: new Map() });
  vi.stubGlobal(
    "Highlight",
    class extends Set<Range> {
      constructor(...ranges: Range[]) {
        super(ranges);
      }
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("searches in both directions and restores the previous control on close", () => {
  const find = vi.fn().mockReturnValueOnce(true).mockReturnValueOnce(false).mockReturnValue(true);
  vi.stubGlobal("pywebview", { api: {} });
  vi.stubGlobal("find", find);
  render(
    <>
      <button>Original control</button>
      <DesktopFind />
    </>,
  );
  const original = screen.getByRole("button", { name: "Original control" });
  original.focus();
  fireEvent.keyDown(window, { key: "f", metaKey: true });
  const input = screen.getByRole("searchbox", { name: "Find in this view" });
  expect(document.activeElement).toBe(input);
  fireEvent.change(input, { target: { value: "resonance" } });
  fireEvent.submit(screen.getByRole("search"));
  expect(find).toHaveBeenLastCalledWith("resonance", false, false, true);
  expect(screen.getByRole("status").textContent).toBe("Match found");
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(find).toHaveBeenLastCalledWith("resonance", false, true, true);
  expect(screen.getByRole("status").textContent).toBe("No matches");
  fireEvent.keyDown(window, { key: "g", metaKey: true });
  expect(find).toHaveBeenLastCalledWith("resonance", false, false, true);
  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("search")).toBeNull();
  expect(document.activeElement).toBe(original);
  act(() => {
    window.dispatchEvent(new Event("scopecat:find"));
  });
  expect(screen.getByRole("searchbox")).toHaveProperty("value", "resonance");
});

it("leaves browser search alone and reports an unavailable native engine", () => {
  render(<DesktopFind />);
  const key = new KeyboardEvent("keydown", { key: "f", ctrlKey: true, cancelable: true });
  window.dispatchEvent(key);
  expect(key.defaultPrevented).toBe(false);
  expect(screen.queryByRole("search")).toBeNull();
  vi.stubGlobal("pywebview", { api: {} });
  vi.stubGlobal("find", undefined);
  act(() => {
    window.dispatchEvent(new Event("pywebviewready"));
  });
  act(() => {
    window.dispatchEvent(new Event("scopecat:find"));
  });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "needle" } });
  fireEvent.submit(screen.getByRole("search"));
  expect(screen.getByRole("status").textContent).toBe("Text search is unavailable in this window.");
});

it("excludes the find bar from rendered search content and restores its input", () => {
  vi.stubGlobal("pywebview", { api: {} });
  const find = vi.fn(() => {
    expect(screen.queryByRole("search")).toBeNull();
    expect(screen.getByRole("search", { hidden: true }).hidden).toBe(true);
    return false;
  });
  vi.stubGlobal("find", find);
  render(<DesktopFind />);
  fireEvent.keyDown(window, { key: "f", metaKey: true });
  const input = screen.getByRole("searchbox");
  fireEvent.change(input, { target: { value: "only in the search field" } });
  fireEvent.submit(screen.getByRole("search"));
  expect(find).toHaveBeenCalledWith("only in the search field", false, false, true);
  expect(screen.getByRole("status").textContent).toBe("No matches");
  expect(document.activeElement).toBe(input);
});

it("retains a visible match independently of query focus and resumes from that range", () => {
  vi.stubGlobal("pywebview", { api: {} });
  render(
    <>
      <p>needle in the document</p>
      <DesktopFind />
    </>,
  );
  const range = document.createRange();
  range.setStart(screen.getByText("needle in the document").firstChild!, 0);
  range.setEnd(range.startContainer, 6);
  const selection = window.getSelection()!;
  const find = vi.fn(() => {
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  });
  vi.stubGlobal("find", find);
  fireEvent.keyDown(window, { key: "f", metaKey: true });
  const input = screen.getByRole("searchbox");
  fireEvent.change(input, { target: { value: "needle" } });
  fireEvent.submit(screen.getByRole("search"));
  expect(CSS.highlights.get("desktop-find")?.size).toBe(1);
  expect(document.activeElement).toBe(input);
  selection.removeAllRanges();
  find.mockImplementationOnce(() => {
    expect(selection.toString()).toBe("needle");
    return false;
  });
  fireEvent.submit(screen.getByRole("search"));
  expect(CSS.highlights.has("desktop-find")).toBe(false);
  fireEvent.keyDown(input, { key: "Escape" });
  expect(CSS.highlights.has("desktop-find")).toBe(false);
});
