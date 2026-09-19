// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HelpWorkspace } from "./HelpWorkspace";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows current locations and qualified maintenance steps without discovering a manager", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  render(
    <HelpWorkspace
      reachable
      health={{
        status: "ok",
        projectId: "local:lab",
        projectName: "Experiment lab",
        projectRoot: "/lab/project",
        details: { data_root: "/lab/scientific-data", unrelated_token: "never-display" },
      }}
    />,
  );
  expect(screen.getByText("/lab/scientific-data")).toBeVisible();
  expect(screen.getByText("/lab/project")).toBeVisible();
  expect(screen.getByText("scopecat app")).toBeVisible();
  expect(screen.getByText("scopecat-lab-tools")).toBeVisible();
  expect(screen.queryByText("never-display")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Experiments" })).toHaveAttribute("href", "#launch");
  for (const link of screen
    .getAllByRole("link")
    .filter((candidate) => candidate.getAttribute("href")?.startsWith("https:"))) {
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.getAttribute("href")).toMatch(/^https:\/\/scopecat-project.github.io\/scopecat\//);
  }
  expect(fetch).not.toHaveBeenCalled();
});
it("keeps recovery guidance available while service information is unavailable", () => {
  render(<HelpWorkspace reachable={false} />);
  expect(screen.getByRole("status")).toHaveTextContent("cannot currently be reached");
  expect(screen.getByText("Current service information is unavailable.")).toBeVisible();
  expect(screen.getByText("lab.cmd")).toBeVisible();
});
