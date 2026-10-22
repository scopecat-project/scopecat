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
  const waiting = await screen.findByRole("button", { name: /draft-b · Waiting for resources/ });
  expect(screen.getByText(/Waiting child: child-b/)).toBeVisible();
  expect(screen.getByText("Acquisition outcome unknown")).toBeVisible();
  expect(screen.getByRole("button", { name: /draft-a · Needs attention/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(select).not.toHaveBeenCalled();
  fireEvent.click(waiting);
  expect(select).toHaveBeenCalledWith("b");
});
