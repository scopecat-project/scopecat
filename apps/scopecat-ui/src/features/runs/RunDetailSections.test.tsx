// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunExecutionSegmentPage } from "../../api-contract";
import {
  AnalysisCard,
  ExecutionSegmentsCard,
  ResourceCard,
  ProgressCard,
} from "./RunDetailSections";

import type { ProjectRun } from "../../types";

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
  expect(screen.getByTestId("publication-id")).toHaveTextContent("older-publication");
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

it("shows the exact publication identity even when a run analysis has a reusable key", async () => {
  vi.spyOn(runApi, "getRunAnalysis").mockResolvedValue({
    id: "retained-publication-1",
    title: "Grouped curves",
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
        analyses={[
          {
            id: "retained-publication-1",
            revision: 1,
            publicationHash: "a".repeat(64),
            key: "curves",
            title: "Grouped curves",
            publishedAt: "2026-09-08T00:00:00Z",
            inputCount: 0,
            outputCount: 0,
          },
        ]}
        error={null}
        pending={false}
        runId="original-run"
        hasNextPage={false}
        loadingNextPage={false}
        onLoadOlder={() => {}}
      />
    </QueryClientProvider>,
  );
  const details = screen.getByText("Grouped curves").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(await screen.findByTestId("publication-id")).toHaveTextContent("retained-publication-1");
  client.clear();
});

describe("Run receipt summary", () => {
  const run = {
    runId: "run-progress",
    status: "running",
    stateLabel: "Running",
    progressCompleted: 9,
    plan: { pointCount: 10 },
    pointPlan: { acceptedPointCount: 10, closed: true },
  } as ProjectRun;

  it("keeps receipt counts separate from recovery coverage and from success", () => {
    render(
      <ProgressCard
        run={run}
        events={[]}
        measurements={{ items: [], recordCount: 10, durableRecordCount: 4 }}
      />,
    );
    const summary = screen.getByRole("region", { name: "Run progress" });
    expect(summary).toHaveTextContent("Received records 10");
    expect(summary).toHaveTextContent("Saved records 4");
    expect(summary).toHaveTextContent("6 received records awaiting save");
    expect(summary).toHaveTextContent("waiting for execution and saving to finish");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText("Succeeded")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Execution evidence", { exact: true }));
    expect(screen.getByText(/9 points have durable execution evidence/)).toBeVisible();
  });

  it("does not invent receipts from coverage, and marks failed live refreshes", () => {
    render(<ProgressCard run={run} events={[]} receiptError={new Error("disconnected")} />);
    expect(screen.getByText(/Device acquisition progress is not inferred/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Displayed counts and plots may be stale");
    expect(screen.getByRole("region", { name: "Run progress" })).not.toHaveTextContent(
      "Received records 9",
    );
  });
});
