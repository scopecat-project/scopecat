// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ProcedureHistory } from "./ProcedureHistory";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows submitted wait and uncertainty without implying queue order or changing selection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        items: [
          {
            procedure_run_id: "b",
            definition: { id: "draft-b" },
            state: "ready",
            resource_wait: { run_id: "child-b", step_key: "acquire" },
            created_at: "2026-10-22",
          },
          {
            procedure_run_id: "a",
            definition: { id: "draft-a" },
            state: "attention_required",
            attention_reason: "Acquisition outcome unknown",
            created_at: "2026-10-21",
          },
        ],
        next_cursor: null,
      }),
    ),
  );
  const select = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ProcedureHistory mode="submitted" selectedId="a" onSelect={select} />
    </QueryClientProvider>,
  );
  const waiting = await screen.findByRole("button", { name: /draft-b · 等待资源/ });
  expect(screen.getByText(/Waiting child: child-b/)).toBeVisible();
  expect(screen.getByText("Acquisition outcome unknown")).toBeVisible();
  expect(screen.getByRole("button", { name: /draft-a · 需要处理/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(select).not.toHaveBeenCalled();
  fireEvent.click(waiting);
  expect(select).toHaveBeenCalledWith("b");
});

it("keeps earlier pages and the inspected selection while loading more submitted tasks", async () => {
  const cursors: Array<string | null> = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    const cursor = new URL(request.url).searchParams.get("cursor");
    cursors.push(cursor);
    return Response.json({
      items: [
        {
          procedure_run_id: cursor ? "earlier" : "selected",
          definition: { id: cursor ? "older-task" : "current-task" },
          state: "ready",
          created_at: "2026-10-22",
        },
      ],
      next_cursor: cursor ? null : 10,
    });
  });
  const select = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ProcedureHistory mode="submitted" selectedId="selected" onSelect={select} />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "加载更早任务" }));
  await screen.findByRole("button", { name: /older-task/ });
  expect(screen.getByRole("button", { name: /current-task/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(screen.queryByRole("button", { name: "加载更早任务" })).not.toBeInTheDocument();
  expect(cursors).toEqual([null, "10"]);
  expect(select).not.toHaveBeenCalled();
});
