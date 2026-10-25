// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceNavigation } from "./WorkspaceNavigation";

afterEach(cleanup);
it("connects the actual native probe labels to product destinations", () => {
  const source = readFileSync("../../scripts/verify_native_windows.py", "utf8");
  const labels = [...source.matchAll(/click_navigation\(first\.window, "([^"]+)"\)/g)].map(
    (match) => match[1],
  );
  expect(new Set(labels).size).toBe(2);
  const select = vi.fn();
  render(
    <WorkspaceNavigation
      view="launch"
      onSelect={select}
      focused={false}
      onShowNavigation={() => {}}
    />,
  );
  for (const label of labels) {
    fireEvent.click(screen.getByRole("button", { name: label }));
  }
  expect(select.mock.calls.map(([view]) => view)).toEqual([
    "decisions",
    "runs",
    "decisions",
    "runs",
  ]);
});
