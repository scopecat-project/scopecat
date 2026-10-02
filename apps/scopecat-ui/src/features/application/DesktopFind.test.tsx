// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopFind } from "./DesktopFind";

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
