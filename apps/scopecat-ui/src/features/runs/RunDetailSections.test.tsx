// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunExecutionSegmentPage } from "../../api-contract";
import { ExecutionSegmentsCard, ResourceCard, ProgressCard } from "./RunDetailSections";

import type { ProjectRun } from "../../types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
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

describe("Run receipt summary", () => {
  const run = {
    runId: "run-progress",
    status: "running",
    stateLabel: "执行中",
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
    const summary = screen.getByRole("region", { name: "run 进度" });
    expect(summary).toHaveTextContent("已接收记录 10");
    expect(summary).toHaveTextContent("已保存记录 4");
    expect(summary).toHaveTextContent("6 received records not saved yet");
    expect(summary).not.toHaveTextContent("Planned data received");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("执行证据", { exact: true }));
    expect(screen.getByText(/连续完成前缀 9 个逻辑点/)).toBeVisible();
  });

  it("does not invent receipts from coverage, and marks failed live refreshes", () => {
    render(<ProgressCard run={run} events={[]} receiptError={new Error("disconnected")} />);
    expect(screen.getByText("尚未收到测量数据。")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("当前计数与曲线可能已过时");
    expect(screen.getByRole("region", { name: "run 进度" })).not.toHaveTextContent("已接收记录 9");
  });
});

it.each([
  ["scalar", 4096, 4096],
  ["array", 256, 256],
  ["adaptive", undefined, 12],
])(
  "shows durable logical progress independently of preview records: %s",
  (_kind, total, completed) => {
    const run = {
      runId: "reopened",
      status: "succeeded",
      stateLabel: "已完成",
      progressCompleted: 2,
      completedLogicalPointCount: completed,
      plan: { pointCount: total },
      pointPlan: { acceptedPointCount: 4096, closed: true },
    } as ProjectRun;
    render(
      <ProgressCard
        run={run}
        events={[]}
        measurements={{ items: [], truncated: true, durableRecordCount: 5000 }}
      />,
    );
    const summary = screen.getByRole("region", { name: "run 进度" });
    expect(summary).toHaveTextContent(`已完成逻辑点 ${completed} ／ 计划点 ${total ?? "未知"}`);
    expect(summary).toHaveTextContent("已保存记录 5000");
    expect(summary).not.toHaveTextContent(/已接收记录 \d/);
    fireEvent.click(screen.getByText("执行证据", { exact: true }));
    expect(screen.getByText(/连续完成前缀 2 个逻辑点/)).toBeVisible();
  },
);
