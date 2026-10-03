// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WindowTitle } from "./WindowTitle";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("identifies the active record in both the native window and document", () => {
  document.title = "Scopecat console";
  const setTitle = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("pywebview", { api: { set_window_title: setTitle } });
  const view = render(<WindowTitle title="Alpha · origin · Scopecat" />);
  expect(document.title).toBe("Alpha · origin · Scopecat");
  expect(setTitle).toHaveBeenLastCalledWith(document.title);
  view.rerender(<WindowTitle title="Beta · origin · Scopecat" />);
  expect(document.title).toBe("Beta · origin · Scopecat");
  expect(setTitle).toHaveBeenLastCalledWith(document.title);
  view.unmount();
  expect(document.title).toBe("Scopecat console");
  expect(setTitle).toHaveBeenLastCalledWith("Scopecat");
});

it("also identifies browser views without requiring a native bridge", () => {
  document.title = "Scopecat console";
  const view = render(<WindowTitle title="Alpha · origin · Scopecat" />);
  expect(document.title).toBe("Alpha · origin · Scopecat");
  view.unmount();
  expect(document.title).toBe("Scopecat console");
});
