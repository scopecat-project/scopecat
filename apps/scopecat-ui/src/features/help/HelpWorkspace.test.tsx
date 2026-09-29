// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HelpWorkspace } from "./HelpWorkspace";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {children}
    </QueryClientProvider>
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows current locations and qualified maintenance steps without discovering a manager", () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] })));
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
    { wrapper: Wrapper },
  );
  expect(screen.getByText("/lab/scientific-data")).toBeVisible();
  expect(screen.getByRole("link", { name: "Application settings" })).toHaveAttribute(
    "href",
    "#settings",
  );
  expect(screen.queryByText("never-display")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Experiments" })).toHaveAttribute("href", "#launch");
  for (const link of screen
    .getAllByRole("link")
    .filter((candidate) => candidate.getAttribute("href")?.startsWith("https:"))) {
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.getAttribute("href")).toMatch(/^https:\/\/scopecat-project.github.io\/scopecat\//);
  }
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("keeps recovery guidance available while service information is unavailable", () => {
  render(<HelpWorkspace reachable={false} />, { wrapper: Wrapper });
  expect(screen.getByRole("status")).toHaveTextContent("cannot currently be reached");
  expect(screen.getByText(/Reopen Scopecat/)).toBeVisible();
});
