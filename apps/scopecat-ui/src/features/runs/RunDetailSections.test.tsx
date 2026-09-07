// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunExecutionSegmentPage } from "../../api-contract";
import { AnalysisCard, ExecutionSegmentsCard, ResourceCard } from "./RunDetailSections";

import * as runApi from "./run-api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

it("opens the exact linked run analysis outside the history page", async () => {
  window.history.replaceState(null, "", "/?run-analysis=older-publication#runs");
  const get = vi.spyOn(runApi, "getRunAnalysis").mockResolvedValue({
    id: "older-publication",
    title: "Retained run evidence",
    revision: 1,
    publicationHash: "a".repeat(64),
    publishedAt: "2026-09-08T00:00:00Z",
    subject: "run",
    inputs: [],
    executions: [],
    outputs: [],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AnalysisCard
        analyses={[]}
        error={null}
        pending={false}
        runId="original-run"
        hasNextPage={false}
        loadingNextPage={false}
        onLoadOlder={() => {}}
      />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("heading", { name: "Retained run evidence" })).toBeVisible();
  expect(screen.queryByText("No analyses saved")).toBeNull();
  expect(get).toHaveBeenCalledWith("original-run", "older-publication", expect.any(AbortSignal));
  client.clear();
});

describe("ResourceCard", () => {
  it("identifies the competing run and reconciliation requirement", () => {
    render(
      <ResourceCard
        run={{
          resources: [
            {
              id: "drive",
              kind: "instrument",
              status: "blocked",
              blockedBy: { ownerKind: "run", ownerId: "run-owner", status: "quarantined" },
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Blocked")).toBeVisible();
    expect(screen.getByText(/Blocked by run run-owner/)).toBeVisible();
    expect(screen.getByText(/reconciliation required/)).toBeVisible();
  });

  it("identifies an interactive session without implying automatic execution", () => {
    render(
      <ResourceCard
        run={{
          resources: [
            {
              id: "drive",
              kind: "instrument",
              status: "blocked",
              blockedBy: {
                ownerKind: "instrument_session",
                ownerId: "session-owner",
                status: "active",
              },
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/Blocked by interactive session session-owner/)).toBeVisible();
    expect(screen.queryByText(/reconciliation required/)).not.toBeInTheDocument();
  });
});

describe("ExecutionSegmentsCard", () => {
  it("shows resume boundaries in execution order", () => {
    const page: RunExecutionSegmentPage = {
      items: [
        segment({
          ordinal: 1,
          segment_id: "segment-2",
          executor_id: "executor-after-restart",
          start_point_count: 12,
        }),
        segment({
          ordinal: 0,
          segment_id: "segment-1",
          executor_id: "executor-before-restart",
          start_point_count: 0,
          ended_at: "2026-08-23T03:30:00Z",
          end_point_count: 12,
          result: "interrupted",
          certainty: "known",
          reason: "executor_lease_expired",
        }),
      ],
      next_cursor: null,
    };

    render(<ExecutionSegmentsCard page={page} error={null} pending={false} />);

    const card = screen.getByTestId("execution-segments-card");
    const items = within(card).getAllByRole("listitem");
    expect(within(items[0]!).getByText("Segment 1")).toBeVisible();
    expect(within(items[0]!).getByText("0 → 12")).toBeVisible();
    expect(within(items[0]!).getByText("Interrupted")).toBeVisible();
    expect(within(items[0]!).getByText(/Executor Lease Expired/)).toBeVisible();
    expect(within(items[1]!).getByText("Segment 2")).toBeVisible();
    expect(within(items[1]!).getByText("12 → active")).toBeVisible();
    expect(within(items[1]!).getByText("Active")).toBeVisible();
  });
});

function segment(
  overrides: Partial<RunExecutionSegmentPage["items"][number]>,
): RunExecutionSegmentPage["items"][number] {
  return {
    sequence: 1,
    segment_id: "segment",
    run_id: "run-1",
    ordinal: 0,
    executor_id: "executor",
    run_contract_fingerprint: "a".repeat(64),
    started_at: "2026-08-23T03:00:00Z",
    start_point_count: 0,
    ...overrides,
  };
}
