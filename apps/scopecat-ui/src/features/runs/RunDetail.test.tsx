// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import type { ComponentProps } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunDetail } from "./RunDetail";

// Keep the parent reconciliation and real analysis controls; unrelated data panes
// do not need their own API responses in this regression.
vi.mock("./RunDetailSections", () => ({
  DataCard: () => null,
  ExecutionSegmentsCard: () => null,
  ProgressCard: () => null,
  ResourceCard: () => null,
  TimelineCard: () => null,
}));
vi.mock("../proposals/RunProposals", () => ({ RunProposals: () => <div>Proposals</div> }));
vi.mock("./RunAnalysisFollows", () => ({ RunAnalysisFollows: () => null }));
vi.mock("./RunSnapshotDetails", () => ({ RunSnapshotDetails: () => null }));
vi.mock("./RunMeasuredCosts", () => ({ RunMeasuredCosts: () => null }));
vi.mock("./RunDomainDecisionCard", () => ({ RunDomainDecisionCard: () => null }));
vi.mock("../launch/PlanOrigin", () => ({ RunPlanOrigin: () => null }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps one analysis entry across repeated run changes and analyzes only the current run", async () => {
  const submitted: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "POST") {
        submitted.push(await request.json());
        return Response.json({
          analysis_id: "analysis-result",
          code_revision: { content_hash: "sha256:" + "a".repeat(64) },
        });
      }
      const match = new URL(request.url).pathname.match(/\/runs\/([^/]+)\/analysis-actions$/);
      if (!match) throw new Error(`Unexpected request: ${request.url}`);
      return Response.json({
        run_id: decodeURIComponent(match[1]!),
        workspace_id: "synthetic-workspace",
        code_revision: { content_hash: "sha256:" + "a".repeat(64) },
        experiment: "signal",
        actions: [
          { title: "Inspect retained signal", analysis: "signal.analysis:inspect", inputs: [] },
        ],
      });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props: Omit<ComponentProps<typeof RunDetail>, "run"> = {
    collectionNames: new Map(),
    events: [],
    eventsError: null,
    eventsPending: false,
    executionSegmentsError: null,
    executionSegmentsPending: false,
    domainDecisionsError: null,
    domainDecisionsPending: false,
    measurementsError: null,
    measurementsPending: false,
    measurementSliceError: null,
    measurementSlicePending: false,
    tracePlans: [],
    traceError: null,
    tracePending: false,
    onTracePlanChange: vi.fn(),
    onMeasurementEntitySelectionChange: vi.fn(),
    measurementFixedAxisIndices: {},
    onMeasurementSliceOffsetChange: vi.fn(),
    onMeasurementFixedAxisIndexChange: vi.fn(),
    contentsError: null,
    contentsPending: false,
    contentsHasNextPage: false,
    contentsLoadingNextPage: false,
    onLoadOlderContents: vi.fn(),
    attentionError: null,
    attentionPending: false,
    onResolveAttention: vi.fn(),
  };
  const view = (runId: string) => (
    <QueryClientProvider client={client}>
      <RunDetail
        {...props}
        run={{
          runId,
          experimentId: "signal",
          status: "succeeded",
          stateLabel: "Succeeded",
          samples: [],
          resources: [],
          tags: [],
          contents: [],
          pointPlan: {
            initialPointCount: 1,
            acceptedPointCount: 1,
            pointLimit: 1,
            decisionCount: 0,
            optimizerAttemptCount: 0,
            operatorRequestCount: 0,
            closed: true,
          },
          plan: {
            pointCount: 1,
            initialPointCount: 1,
            pointLimit: 1,
            coordinateIds: [],
            coordinateSpecs: [],
            adaptiveCoordinateIds: [],
            adaptiveRegionCount: 0,
            adaptiveRegions: [],
            adaptiveRegionsTruncated: false,
            sampledPoints: [],
            sampledPointsTruncated: false,
            recordIds: [],
          },
        }}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(view("A"));
  for (const runId of ["A", "B", "C", "A", "B", "C", "A"]) {
    rerender(view(runId));
    expect(screen.getAllByText("Analyze this result", { selector: "summary" })).toHaveLength(1);
    const details = screen.getByText("Analyze this result", { selector: "summary" }).parentElement!;
    details.setAttribute("open", "");
    fireEvent(details, new Event("toggle"));
    fireEvent.click(await screen.findByRole("button", { name: "Run Inspect retained signal" }));
    await waitFor(() =>
      expect(submitted.at(-1)).toEqual({
        run_id: runId,
        workspace_id: "synthetic-workspace",
        code_revision: { content_hash: "sha256:" + "a".repeat(64) },
        analysis: "signal.analysis:inspect",
        arguments: {},
      }),
    );
    await screen.findByRole("link", { name: "View saved result" });
  }
  expect(submitted).toHaveLength(7);
  client.clear();
});
