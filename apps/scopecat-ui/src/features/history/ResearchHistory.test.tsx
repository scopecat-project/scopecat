// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ResearchHistory } from "./ResearchHistory";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("filters retained runs and removes only their project association", async () => {
  const requests: Request[] = [];
  const open = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/research-projects")
        return Response.json({
          items: [
            {
              id: "alpha",
              name: "Alpha",
              description: "",
              revision: 1,
              created_at: "2026-09-17T00:00:00Z",
              updated_at: "2026-09-17T00:00:00Z",
            },
          ],
        });
      if (url.pathname === "/api/v1/samples")
        return Response.json({
          items: [{ record: { id: "chip" }, revision: { content: { display_name: "Chip" } } }],
        });
      if (url.pathname.endsWith("/members/samples")) return Response.json({ ids: ["chip"] });
      if (request.method === "DELETE")
        return Response.json({
          project_id: "alpha",
          kind: "runs",
          identity: "run-1",
          present: false,
        });
      if (url.pathname === "/api/v1/runs")
        return Response.json({
          items: [
            {
              snapshot: { run_id: "run-1", created_at: "2026-09-17T00:00:00Z", samples: [] },
              control: { state: "closed", admission: { display_name: "Retained scan" } },
              deployment_id: "bench-1",
            },
          ],
        });
      throw new Error(`Unexpected request: ${request.method} ${url.pathname}`);
    }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <ResearchHistory daemonUnavailable={false} onOpenRun={open} />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: "Alpha" });
  fireEvent.change(screen.getByLabelText("Research project"), { target: { value: "alpha" } });
  fireEvent.change(screen.getByLabelText("Sample", { exact: true }), { target: { value: "chip" } });
  await waitFor(() =>
    expect(
      requests.some((request) => {
        const url = new URL(request.url);
        return (
          url.pathname === "/api/v1/runs" &&
          url.searchParams.get("research_project") === "alpha" &&
          url.searchParams.get("sample_id") === "chip"
        );
      }),
    ).toBe(true),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Retained scan" }));
  expect(open).toHaveBeenCalledWith("run-1");
  fireEvent.click(screen.getByRole("button", { name: "Remove run association" }));
  await waitFor(() =>
    expect(
      requests.some(
        (request) =>
          request.method === "DELETE" &&
          new URL(request.url).pathname === "/api/v1/research-projects/alpha/members/runs/run-1",
      ),
    ).toBe(true),
  );
  expect(
    requests.some(
      (request) =>
        request.method === "DELETE" && new URL(request.url).pathname === "/api/v1/runs/run-1",
    ),
  ).toBe(false);
});
