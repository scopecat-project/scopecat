// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { DecisionRuns } from "./DecisionRuns";
vi.mock("../runs/run-api", () => ({
  getRun: vi.fn(async (id: string) => ({ runId: id, sequence: 3 })),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("pages exact decision references separately and isolates identical IDs across spaces", async () => {
  let space = "a";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      expect(url.pathname).toBe("/api/v1/runs/source/analyses/publication/decision-runs");
      expect(url.searchParams.get("publication_hash")).toBe("sha256:exact");
      return Response.json(
        space === "a"
          ? url.searchParams.has("before")
            ? { run_ids: ["child-a", "older"], next_cursor: null }
            : { run_ids: ["child-a"], next_cursor: 2 }
          : { run_ids: [], next_cursor: null },
      );
    }),
  );
  const open = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const view = () => (
    <QueryClientProvider client={client}>
      <DecisionRuns
        projectId={space}
        runId="source"
        analysisId="publication"
        publicationHash="sha256:exact"
        onOpenRun={open}
      />
    </QueryClientProvider>
  );
  const mounted = render(view());
  fireEvent.click(await screen.findByTitle("child-a"));
  expect(open).toHaveBeenCalledWith("child-a");
  fireEvent.click(screen.getByRole("button", { name: "Load older follow-up experiments" }));
  await screen.findByTitle("older");
  expect(screen.getAllByTitle("child-a")).toHaveLength(1);
  space = "b";
  mounted.rerender(view());
  await screen.findByText("No admitted runs yet.");
  await waitFor(() => expect(screen.queryByTitle("child-a")).not.toBeInTheDocument());
  expect(screen.getByText(/not inputs to this analysis/)).toBeVisible();
});
