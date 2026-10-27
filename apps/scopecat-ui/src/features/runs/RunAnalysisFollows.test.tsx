// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { RunAnalysisFollows } from "./RunAnalysisFollows";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("refreshes retained results and stops analysis without cancelling acquisition", async () => {
  let stopped = false;
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(`${request.method} ${new URL(request.url).pathname}`);
      if (request.method === "POST") stopped = true;
      const view = {
        request: { id: "follow", analysis: { analysis: "authors:fit", run_id: "scan" } },
        state: stopped ? "stopped" : "running",
        finished_count: 1,
        group_count: 2,
        failed_count: 0,
      };
      return Response.json(request.method === "POST" ? view : [view]);
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const refresh = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <RunAnalysisFollows runId="scan" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/进行中 · 1 \/ 2 组/)).toBeVisible();
  await waitFor(() =>
    expect(refresh).toHaveBeenCalledWith({ queryKey: ["analyses", "run", "scan"] }),
  );
  fireEvent.click(screen.getByRole("button", { name: "停止分析" }));
  expect(await screen.findByText(/已停止 · 1 \/ 2 组/)).toBeVisible();
  expect(requests.filter((value) => value.startsWith("POST"))).toEqual([
    "POST /api/v1/analysis-follows/follow/stop",
  ]);
  client.clear();
});

it.each([
  ["completed", "处理完成"],
  ["attention", "需要检查"],
  ["stopped", "已停止"],
])("reads %s without restarting analysis or acquisition", async (state, label) => {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(`${request.method} ${new URL(request.url).pathname}`);
      return Response.json([
        {
          request: { id: "follow", analysis: { analysis: "authors:fit", run_id: "scan" } },
          state,
          finished_count: 1,
          group_count: 2,
          failed_count: 1,
          error: "Authored error: uncertain publication sha256:original",
        },
      ]);
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunAnalysisFollows runId="scan" />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("status")).toHaveTextContent(`${label} · 1 / 2 组 · 1 组需要检查`);
  expect(screen.getByText("Authored error: uncertain publication sha256:original")).toBeVisible();
  expect(screen.queryByRole("button", { name: "停止分析" })).not.toBeInTheDocument();
  expect(requests).toEqual(["GET /api/v1/runs/scan/analysis-follows"]);
  client.clear();
});
