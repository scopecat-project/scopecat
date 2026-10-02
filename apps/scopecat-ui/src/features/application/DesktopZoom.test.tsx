// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopZoom } from "./DesktopZoom";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shares native menu and keyboard zoom within the current window", () => {
  render(<DesktopZoom />);
  const browserKey = new KeyboardEvent("keydown", { key: "+", ctrlKey: true, cancelable: true });
  window.dispatchEvent(browserKey);
  expect(browserKey.defaultPrevented).toBe(false);
  expect(document.documentElement.style.zoom).toBe("");
  vi.stubGlobal("pywebview", { api: {} });
  act(() => {
    window.dispatchEvent(new Event("pywebviewready"));
  });
  fireEvent.keyDown(window, { key: "+", metaKey: true, shiftKey: true });
  expect(document.documentElement.style.zoom).toBe("110%");
  act(() => {
    window.dispatchEvent(new Event("scopecat:zoom-in"));
  });
  expect(screen.getByRole("status").textContent).toBe("Zoom 125%");
  fireEvent.keyDown(window, { key: "-", ctrlKey: true });
  expect(document.documentElement.style.zoom).toBe("110%");
  fireEvent.keyDown(window, { key: "0", metaKey: true });
  expect(document.documentElement.style.zoom).toBe("100%");
  act(() => {
    for (let index = 0; index < 20; index++) window.dispatchEvent(new Event("scopecat:zoom-out"));
  });
  expect(document.documentElement.style.zoom).toBe("50%");
  act(() => {
    window.dispatchEvent(new Event("scopecat:zoom-reset"));
  });
  expect(document.documentElement.style.zoom).toBe("100%");
  cleanup();
  expect(document.documentElement.style.zoom).toBe("");
});
