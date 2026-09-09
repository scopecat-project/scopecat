// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { getEvents, getHealth } from "./data/project-api";
import {
  getMeasurementLivePreview,
  getMeasurementPreview,
  getMeasurementSlice,
  getMeasurementTracePreview,
  getOlderRuns,
  getRun,
  getRunAnalysisSummaries,
  getRunEvents,
  getRuns,
} from "./features/runs/run-api";
import type { MeasurementDatasetSchema, MeasurementRecord } from "./api-contract";
import type { ProjectRun } from "./types";

vi.mock("./data/project-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./data/project-api")>()),
  getEvents: vi.fn(),
  getHealth: vi.fn(),
}));

vi.mock("./features/runs/run-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./features/runs/run-api")>()),
  getMeasurementLivePreview: vi.fn(),
  getMeasurementPreview: vi.fn(),
  getMeasurementSlice: vi.fn(),
  getMeasurementTracePreview: vi.fn(),
  getOlderRuns: vi.fn(),
  getRun: vi.fn(),
  getRunAnalysisSummaries: vi.fn(),
  getRunEvents: vi.fn(),
  getRuns: vi.fn(),
}));

vi.mock("./features/config/ConfigWorkspace", () => ({
  ConfigWorkspace: ({ onOpenRun }: { onOpenRun?: (runId: string) => void }) => (
    <>
      <button type="button" onClick={() => onOpenRun?.("run-2")}>
        Open listed producing run
      </button>
      <button type="button" onClick={() => onOpenRun?.("run-archive")}>
        Open unlisted producing run
      </button>
    </>
  ),
}));

vi.mock("./features/analyses/AnalysesWorkspace", () => ({
  AnalysesWorkspace: ({
    onOpenRun,
    onSelectAnalysis,
    selectedAnalysisId,
  }: {
    onOpenRun: (runId: string) => void;
    onSelectAnalysis: (analysisId: string) => void;
    selectedAnalysisId?: string;
  }) => (
    <div>
      Project analysis workspace
      <span>Selected analysis {selectedAnalysisId ?? "none"}</span>
      <button type="button" onClick={() => onOpenRun("run-2")}>
        Open analysis input run
      </button>
      <button type="button" onClick={() => onSelectAnalysis("analysis-next")}>
        Select next analysis
      </button>
    </div>
  ),
}));

vi.mock("./features/samples/SamplesWorkspace", () => ({
  SamplesWorkspace: ({
    onOpenRun,
    onSelectSample,
    selectedSampleId,
  }: {
    onOpenRun: (runId: string) => void;
    onSelectSample: (sampleId: string) => void;
    selectedSampleId?: string;
  }) => (
    <div>
      Sample workspace
      <span>Selected sample {selectedSampleId ?? "none"}</span>
      <button type="button" onClick={() => onOpenRun("run-2")}>
        Open sample run
      </button>
      <button type="button" onClick={() => onSelectSample("chip-b22")}>
        Select next sample
      </button>
    </div>
  ),
}));

vi.mock("./features/instruments/InstrumentsWorkspace", () => ({
  InstrumentsWorkspace: () => <div>Instrument workspace</div>,
}));

vi.mock("./features/proposals/RunProposals", () => ({
  RunProposals: () => <div>Proposal details</div>,
}));

vi.mock("./features/launch/LaunchWorkspace", () => ({
  LaunchWorkspace: () => <div>Calibration launcher</div>,
}));

const RUNS = [projectRun("run-1"), projectRun("run-2")];
let projectEventListener: ((event: Event) => void) | undefined;
let openEventListener: ((event: Event) => void) | undefined;

beforeEach(() => {
  projectEventListener = undefined;
  openEventListener = undefined;
  window.history.replaceState(null, "", "#configuration");
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal(
    "EventSource",
    class {
      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        const callback = (event: Event) => {
          if (typeof listener === "function") {
            listener(event);
          } else {
            listener.handleEvent(event);
          }
        };
        if (type === "open") openEventListener = callback;
        if (type === "project") projectEventListener = callback;
      }
      removeEventListener(type: string) {
        if (type === "open") openEventListener = undefined;
        if (type === "project") projectEventListener = undefined;
      }
      close() {}
    },
  );
  vi.mocked(getHealth).mockResolvedValue({
    status: "ok",
    projectId: "local:test",
    projectName: "Test lab",
    projectRoot: "/tmp/test-lab",
    details: {},
  });
  vi.mocked(getRuns).mockResolvedValue({ items: RUNS });
  vi.mocked(getOlderRuns).mockResolvedValue({ items: [] });
  vi.mocked(getRun).mockImplementation(async (runId) => projectRun(runId));
  vi.mocked(getEvents).mockResolvedValue([]);
  vi.mocked(getRunEvents).mockResolvedValue([]);
  vi.mocked(getMeasurementLivePreview).mockResolvedValue({
    active: false,
    receivedRecordCount: 0,
    durableRecordCount: 0,
  });
  vi.mocked(getMeasurementPreview).mockResolvedValue({ items: [] });
  vi.mocked(getMeasurementSlice).mockResolvedValue({
    items: [],
    selectedPointCount: 0,
    offset: 0,
    windowPointCount: 0,
    truncated: false,
  });
  vi.mocked(getMeasurementTracePreview).mockResolvedValue(tracePreview("magnitude"));
  vi.mocked(getRunAnalysisSummaries).mockResolvedValue({ items: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("config provenance navigation", () => {
  it("restores a sample deep link and opens its run", async () => {
    window.history.replaceState(null, "", "/?sample=chip-a17#samples");
    renderApp();

    expect(await screen.findByText("Selected sample chip-a17")).toBeVisible();
    expect(screen.getByRole("button", { name: "Samples" })).toHaveAttribute("aria-current", "page");

    fireEvent.click(screen.getByRole("button", { name: "Select next sample" }));
    expect(window.location.search).toBe("?sample=chip-b22");
    expect(window.location.hash).toBe("#samples");

    fireEvent.click(screen.getByRole("button", { name: "Open sample run" }));
    expect(window.location.search).toBe("?run=run-2");
    expect(window.location.hash).toBe("");
  });

  it("opens an exact run binding in the sample workspace", async () => {
    const run = projectRun("run-1");
    run.samples = [
      {
        role: "subject",
        sample_id: "chip-a17",
        revision: 2,
        content_hash: `sha256:${"a".repeat(64)}`,
        kind: "chip",
        display_name: "Chip A17",
      },
    ];
    vi.mocked(getRuns).mockResolvedValue({ items: [run] });
    vi.mocked(getRun).mockResolvedValue(run);
    window.history.replaceState(null, "", "/?run=run-1");
    renderApp();

    fireEvent.click(await screen.findByTitle("chip-a17 · exact revision 2"));

    expect(await screen.findByText("Selected sample chip-a17")).toBeVisible();
    expect(window.location.search).toBe("?sample=chip-a17&sample-revision=2");
    expect(window.location.hash).toBe("#samples");
  });

  it("restores the analyses route and opens an input in the Runs view", async () => {
    window.history.replaceState(null, "", "/#analyses");
    renderApp();

    expect(await screen.findByText("Project analysis workspace")).toBeVisible();
    expect(screen.getByRole("button", { name: "Analyses" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(getRuns).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Open analysis input run" }));

    expect(screen.getByRole("button", { name: "Runs" })).toHaveAttribute("aria-current", "page");
    expect(window.location.search).toBe("?run=run-2");
    expect(window.location.hash).toBe("");
  });

  it("restores and updates an exact analysis deep link", async () => {
    window.history.replaceState(null, "", "/?analysis=analysis-review-r2#analyses");
    renderApp();

    expect(await screen.findByText("Selected analysis analysis-review-r2")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Select next analysis" }));

    expect(window.location.search).toBe("?analysis=analysis-next");
    expect(window.location.hash).toBe("#analyses");
  });

  it("restores and updates the Instruments hash route", async () => {
    window.history.replaceState(null, "", "/#instruments");
    renderApp();

    expect(await screen.findByText("Instrument workspace")).toBeVisible();
    expect(screen.getByRole("button", { name: "Instruments" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(getRuns).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
    expect(window.location.hash).toBe("#configuration");
    fireEvent.click(screen.getByRole("button", { name: "Instruments" }));
    expect(window.location.hash).toBe("#instruments");
  });

  it("invalidates canonical queries after project events", async () => {
    window.history.replaceState(null, "", "/#instruments");
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderApp(queryClient);
    await screen.findByText("Instrument workspace");
    await waitFor(() => expect(projectEventListener).toBeDefined());

    invalidate.mockClear();
    act(() => emitProjectEvent("run-1", "instrument_session_opened"));

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["instruments"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sample-revisions"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run-contents"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run-content"] });
    });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["experiment-launcher"] });
  });

  it("does not mount the run browser while configuration is active", async () => {
    renderApp();

    await screen.findByRole("button", { name: "Open listed producing run" });
    expect(getRuns).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("#configuration");
    expect(screen.getByRole("button", { name: "Configuration" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("opens the producing run and selects it in the existing Runs view", async () => {
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "Open listed producing run" }));

    expect(screen.getByRole("button", { name: "Runs" })).toHaveAttribute("aria-current", "page");
    await waitFor(() =>
      expect(screen.getByTitle("Inspect run run-2")).toHaveAttribute("aria-current", "true"),
    );
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?run=run-2");
    await waitFor(() =>
      expect(getRunEvents).toHaveBeenCalledWith("run-2", expect.any(AbortSignal)),
    );
  });

  it("preserves a producing run that is outside the latest run index", async () => {
    renderApp();

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Open unlisted producing run",
      }),
    );

    expect(screen.getByRole("button", { name: "Runs" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByTitle("run-archive")).toHaveTextContent("run-archive");
    expect(screen.getByTitle("Inspect run run-1")).not.toHaveAttribute("aria-current");
    expect(screen.getByTitle("Inspect run run-2")).not.toHaveAttribute("aria-current");
  });

  it("selects the first indexed run when no explicit run was requested", async () => {
    window.history.replaceState(null, "", "/");

    renderApp();

    await waitFor(() =>
      expect(screen.getByTitle("Inspect run run-1")).toHaveAttribute("aria-current", "true"),
    );
    expect(screen.getByTitle("run-1")).toHaveTextContent("run-1");
    expect(window.location.search).toBe("?run=run-1");
  });

  it("restores the selected run from the URL", async () => {
    window.history.replaceState(null, "", "/?run=run-2#configuration");

    renderApp();

    expect(screen.getByRole("button", { name: "Configuration" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    fireEvent.click(screen.getByRole("button", { name: "Runs" }));
    await waitFor(() =>
      expect(screen.getByTitle("Inspect run run-2")).toHaveAttribute("aria-current", "true"),
    );
    expect(screen.getByTitle("run-2")).toHaveTextContent("run-2");
  });

  it("loads and merges older run pages", async () => {
    window.history.replaceState(null, "", "/");
    vi.mocked(getRuns).mockResolvedValue({
      items: RUNS.map((run, index) => ({ ...run, sequence: index + 20 })),
      nextCursor: 20,
    });
    vi.mocked(getOlderRuns).mockResolvedValue({
      items: [
        { ...projectRun("run-old"), sequence: 2 },
        { ...projectRun("run-1"), sequence: 1 },
      ],
    });

    renderApp();
    expect(await screen.findByText("0 active in loaded runs")).toBeVisible();
    expect(screen.getByText("No flags in loaded runs")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "Load older runs" }));

    expect(await screen.findByTitle("Inspect run run-old")).toBeVisible();
    expect(getOlderRuns).toHaveBeenCalledWith(20);
    expect(screen.getAllByTitle("Inspect run run-1")).toHaveLength(1);
    expect(
      screen.getAllByTitle(/^Inspect run /).map((button) => button.getAttribute("title")),
    ).toEqual(["Inspect run run-2", "Inspect run run-1", "Inspect run run-old"]);
    expect(screen.queryByRole("button", { name: "Load older runs" })).not.toBeInTheDocument();
  });

  it("discards loaded history when the latest page head moves", async () => {
    window.history.replaceState(null, "", "/");
    vi.mocked(getRuns)
      .mockResolvedValueOnce({
        items: RUNS.map((run, index) => ({ ...run, sequence: index + 20 })),
        nextCursor: 20,
      })
      .mockResolvedValue({
        items: RUNS.map((run, index) => ({ ...run, sequence: index + 30 })),
        nextCursor: 30,
      });
    vi.mocked(getOlderRuns).mockResolvedValue({
      items: [{ ...projectRun("run-old"), sequence: 2 }],
    });

    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Load older runs" }));
    expect(await screen.findByTitle("Inspect run run-old")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Refresh project data" }));

    await waitFor(() => expect(screen.queryByTitle("Inspect run run-old")).not.toBeInTheDocument());
    fireEvent.click(await screen.findByRole("button", { name: "Load older runs" }));
    await waitFor(() =>
      expect(vi.mocked(getOlderRuns).mock.calls.map(([cursor]) => cursor)).toEqual([20, 30]),
    );
  });

  it("reports a lost daemon even while slower queries still hold data", async () => {
    window.history.replaceState(null, "", "/");
    vi.mocked(getHealth)
      .mockResolvedValueOnce({
        status: "online",
        projectId: "local:test",
        projectName: "Test lab",
        projectRoot: "/tmp/test-lab",
        details: {},
      })
      .mockRejectedValue(new Error("daemon stopped"));
    vi.mocked(getRuns)
      .mockResolvedValueOnce({ items: RUNS })
      .mockRejectedValue(new Error("daemon stopped"));

    renderApp();

    expect((await screen.findAllByText("Online", { exact: true }))[0]).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh project data" }));

    expect(await screen.findByText("Disconnected", { exact: true })).toBeVisible();
    expect(screen.getByText("Daemon unavailable.")).toBeVisible();
  });

  it("uses durable transitions without treating a started point as complete", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const running = {
      ...projectRun("run-1"),
      status: "running" as const,
      stateLabel: "Running",
      plan: {
        coordinateSpecs: [],
        adaptiveCoordinateIds: [],
        adaptiveRegionCount: 0,
        adaptiveRegions: [],
        adaptiveRegionsTruncated: false,
        sampledPoints: [],
        sampledPointsTruncated: false,
        coordinateIds: [],
        recordIds: [],
        pointCount: 3,
        initialPointCount: 3,
        pointLimit: 3,
      },
    };
    vi.mocked(getRuns).mockResolvedValue({ items: [running] });
    vi.mocked(getRun).mockResolvedValue(running);
    vi.mocked(getRunEvents).mockResolvedValue([
      {
        id: 1,
        runId: "run-1",
        kind: "execution_transition_committed",
        payload: {
          stage: "compute",
          state: "started",
          point_index: 2,
          evidence: {},
        },
      },
      {
        id: 2,
        runId: "run-1",
        kind: "execution_transition_committed",
        payload: {
          stage: "append_measurement",
          state: "completed",
          evidence: { start_index: 0, record_count: 1 },
        },
      },
    ]);

    renderApp();

    expect(
      await screen.findByRole("progressbar", {
        name: "1 of 3 points complete",
      }),
    ).toBeVisible();
    expect(screen.getByText("33%")).toBeVisible();
  });

  it("shows adaptive coverage against its point limit", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const running = {
      ...projectRun("run-1"),
      status: "running" as const,
      stateLabel: "Running",
      progressCompleted: 2,
      pointPlan: {
        initialPointCount: 1,
        acceptedPointCount: 3,
        pointLimit: 4,
        decisionCount: 2,
        optimizerAttemptCount: 1,
        operatorRequestCount: 1,
        closed: false,
      },
      plan: {
        coordinateSpecs: [],
        adaptiveCoordinateIds: ["frequency"],
        adaptiveScope: "per_region" as const,
        adaptiveRegionCount: 1,
        adaptiveRegions: [{ id: "region-0", coordinates: {}, initial_point_count: 1 }],
        adaptiveRegionsTruncated: false,
        sampledPoints: [],
        sampledPointsTruncated: false,
        coordinateIds: ["frequency"],
        recordIds: ["signal"],
        initialPointCount: 1,
        pointLimit: 4,
      },
    };
    vi.mocked(getRuns).mockResolvedValue({ items: [running] });
    vi.mocked(getRun).mockResolvedValue(running);

    renderApp();

    const progress = await screen.findByRole("progressbar", {
      name: "2 of 4 points complete",
    });
    expect(progress).toBeVisible();
    expect(progress.closest("article")).toHaveTextContent("2 / 3 points accepted · 4 max");
    expect(progress.closest("article")).toHaveTextContent(
      "Optimizer attempts 1 · operator requests 1 · plan open",
    );
    expect(screen.getByText("Initial / accepted / max points")).toBeVisible();
    expect(screen.getByText("1 / 3 / 4")).toBeVisible();
  });

  it("keeps distinct records in one bounded measurement preview", async () => {
    window.history.replaceState(null, "", "/");
    vi.mocked(getMeasurementPreview).mockResolvedValue({
      items: [measurementRecord(0, 1, "dataset-a"), measurementRecord(0, 2, "dataset-b")],
      truncated: true,
    });

    renderApp();
    expect(await screen.findByText(/2\+ records/)).toBeVisible();
    expect(getMeasurementPreview).toHaveBeenCalledWith("run-1", expect.any(AbortSignal));
    expect(screen.getByTestId("measurement-preview")).toHaveTextContent(
      '"dataset_id": "dataset-b"',
    );
  });

  it("shows the latest daemon-received record before it is durable", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const running = {
      ...projectRun("run-1"),
      status: "running" as const,
      stateLabel: "Running",
      plan: {
        coordinateSpecs: [],
        adaptiveCoordinateIds: [],
        adaptiveRegionCount: 0,
        adaptiveRegions: [],
        adaptiveRegionsTruncated: false,
        sampledPoints: [],
        sampledPointsTruncated: false,
        coordinateIds: [],
        recordIds: [],
        pointCount: 3,
        initialPointCount: 3,
        pointLimit: 3,
      },
    };
    vi.mocked(getRuns).mockResolvedValue({ items: [running] });
    vi.mocked(getRun).mockResolvedValue(running);
    vi.mocked(getMeasurementLivePreview).mockResolvedValue({
      active: true,
      latest: measurementRecord(0, 1.25),
      receivedRecordCount: 1,
      durableRecordCount: 0,
    });

    renderApp();

    expect(await screen.findByText(/^1 records/)).toBeVisible();
    expect(screen.getByText(/visible from daemon memory and is not durable yet/)).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "1 of 3 points complete" })).toBeVisible();
  });

  it("resets the bounded measurement preview for the event's run", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    vi.mocked(getMeasurementPreview).mockResolvedValue({
      items: [measurementRecord(0, 0)],
      truncated: false,
    });

    const queryClient = createQueryClient();
    renderApp(queryClient);
    await waitFor(() => expect(getMeasurementPreview).toHaveBeenCalledTimes(1));
    expect(projectEventListener).toBeDefined();
    queryClient.setQueryData(["measurements", "run-2"], {
      items: [measurementRecord(9, 9)],
      truncated: false,
    });
    queryClient.setQueryData(["measurement-trace", "run-2", "trace", "{}"], {
      stale: true,
    });

    act(() => {
      emitProjectEvent("run-2", "measurement_dataset_initialized");
    });
    await waitFor(() =>
      expect(queryClient.getQueryData(["measurements", "run-2"])).toBeUndefined(),
    );
    expect(queryClient.getQueryData(["measurement-trace", "run-2", "trace", "{}"])).toBeUndefined();
    expect(getMeasurementPreview).toHaveBeenCalledTimes(1);

    act(() => {
      emitProjectEvent("run-1", "measurements_sealed");
    });
    await waitFor(() => expect(getMeasurementPreview).toHaveBeenCalledTimes(2));
    expect(vi.mocked(getMeasurementPreview).mock.calls.map(([runId]) => runId)).toEqual([
      "run-1",
      "run-1",
    ]);
  });

  it("queries only the selected bounded trace mode and authored slice", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const schema = traceDatasetSchema();
    vi.mocked(getMeasurementPreview).mockResolvedValue({ schema, items: [] });
    vi.mocked(getMeasurementSlice).mockResolvedValue({
      items: [],
      selectedPointCount: 4,
      offset: 0,
      windowPointCount: 4,
      truncated: false,
    });
    vi.mocked(getMeasurementTracePreview).mockImplementation(async (_runId, selection) =>
      tracePreview(selection.valueMode),
    );

    renderApp();

    expect(
      await screen.findByRole("img", {
        name: "S21 magnitude: |S21| [ratio] by Frequency [GHz]",
      }),
    ).toBeVisible();
    await waitFor(() =>
      expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
        "run-1",
        {
          observableId: "response",
          coordinateId: "frequency",
          fixedAxisIndices: { bias: 0 },
          valueMode: "magnitude",
        },
        expect.any(AbortSignal),
      ),
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Measurement trace" }), {
      target: { value: "trace:response:frequency:phase" },
    });
    expect(
      await screen.findByRole("img", {
        name: "S21 phase: phase(S21) [rad] by Frequency [GHz]",
      }),
    ).toBeVisible();
    expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
      "run-1",
      expect.objectContaining({ valueMode: "phase", fixedAxisIndices: { bias: 0 } }),
      expect.any(AbortSignal),
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Bias slice" }), {
      target: { value: "1" },
    });
    await waitFor(() =>
      expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
        "run-1",
        expect.objectContaining({ valueMode: "phase", fixedAxisIndices: { bias: 1 } }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("forwards entity chip selection to the bounded trace query", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const schema = entityTraceDatasetSchema();
    vi.mocked(getMeasurementPreview).mockResolvedValue({ schema, items: [] });
    vi.mocked(getMeasurementSlice).mockResolvedValue({
      items: [],
      selectedPointCount: 4,
      offset: 0,
      windowPointCount: 4,
      truncated: false,
    });
    vi.mocked(getMeasurementTracePreview).mockImplementation(async (_runId, selection) =>
      tracePreview(selection.valueMode),
    );

    renderApp();

    const q1 = await screen.findByRole("button", { name: "Qubits Q1" });
    fireEvent.click(q1);

    await waitFor(() =>
      expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
        "run-1",
        expect.objectContaining({
          observableId: "response",
          coordinateId: "frequency",
          entities: [expect.objectContaining({ kind: "qubit", id: "q1" })],
        }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("refreshes canonical queries and the experiment catalog whenever SSE connects", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderApp(queryClient);

    expect(await screen.findByRole("heading", { name: "Recent events" })).toBeVisible();
    await waitFor(() => expect(openEventListener).toBeDefined());
    const initialCounts = canonicalQueryCallCounts();

    act(() => {
      emitSseOpen();
    });
    await waitFor(() =>
      expect(canonicalQueryCallCounts()).toEqual(initialCounts.map((count) => count + 1)),
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["experiment-launcher"] });
    invalidate.mockClear();
    const connectedCounts = canonicalQueryCallCounts();

    act(() => {
      emitSseOpen();
    });
    await waitFor(() =>
      expect(canonicalQueryCallCounts()).toEqual(connectedCounts.map((count) => count + 1)),
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["experiment-launcher"] });
  });

  it("labels the bounded run event timeline honestly", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    vi.mocked(getRunEvents).mockResolvedValue(
      Array.from({ length: 500 }, (_, index) => ({
        id: index + 1,
        runId: "run-1",
        kind: "execution_transition_committed",
        payload: { point_index: index },
      })),
    );

    renderApp();

    expect(await screen.findByRole("heading", { name: "Recent events" })).toBeVisible();
    expect(
      screen.getByText("Showing the latest 500 events; older events are not loaded."),
    ).toBeVisible();
    expect(screen.getAllByText("Execution transition")).toHaveLength(500);
  });
});

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderApp(queryClient = createQueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

function projectRun(runId: string): ProjectRun {
  return {
    runId,
    experimentId: "ramsey",
    tags: [],
    status: "succeeded",
    stateLabel: "Succeeded",
    updatedAt: "2026-07-24T08:00:00Z",
    pointPlan: {
      initialPointCount: 0,
      acceptedPointCount: 0,
      pointLimit: 0,
      decisionCount: 0,
      optimizerAttemptCount: 0,
      operatorRequestCount: 0,
      closed: true,
      stopReason: "static point plan",
    },
    plan: {
      initialPointCount: 0,
      pointLimit: 0,
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
    resources: [],
    samples: [],
    contents: [],
  };
}

function measurementRecord(
  pointIndex: number,
  signal: number,
  datasetId?: string,
): MeasurementRecord {
  return {
    run_id: "run-1",
    logical_point_id: `${datasetId ?? "point"}-${pointIndex}`,
    point_index: pointIndex,
    coordinates: {},
    observables: {
      signal: {
        kind: "scalar",
        dtype: "float64",
        unit: "ratio",
        value: signal,
      },
    },
    metadata: datasetId ? { dataset_id: datasetId } : {},
  };
}

function traceDatasetSchema(): MeasurementDatasetSchema {
  return {
    format_version: "scopecat.measurement_dataset_schema.v17",
    dataset_id: "raw-measurements",
    record_schema: "scopecat.measurement_record.v10",
    point_domain: {
      kind: "product_grid",
      axes: [traceAxis("row", [0, 1]), traceAxis("column", [0, 1]), traceAxis("bias", [0, 1])],
    },
    dimensions: [
      { id: "point", kind: "point", size: 8 },
      { id: "sample", kind: "sample", size: null },
    ],
    variables: [
      {
        id: "row",
        role: "coordinate",
        dtype: "float64",
        dims: ["point"],
      },
      {
        id: "column",
        role: "coordinate",
        dtype: "float64",
        dims: ["point"],
      },
      {
        id: "bias",
        label: "Bias",
        role: "coordinate",
        dtype: "float64",
        unit: "V",
        dims: ["point"],
      },
      {
        id: "frequency",
        label: "Frequency",
        role: "coordinate",
        dtype: "float64",
        unit: "GHz",
        dims: ["point", "sample"],
        recording_group_id: "readout",
      },
      {
        id: "response",
        label: "S21",
        role: "observable",
        dtype: "complex128",
        unit: "ratio",
        dims: ["point", "sample"],
        recording_group_id: "readout",
      },
    ],
    primary_coordinates: ["row", "column", "bias", "frequency"],
    primary_observables: ["response"],
  };
}

function entityTraceDatasetSchema(): MeasurementDatasetSchema {
  const schema = traceDatasetSchema();
  return {
    ...schema,
    dimensions: [
      schema.dimensions[0]!,
      {
        id: "qubit",
        kind: "entity",
        label: "Qubits",
        size: 2,
        index: {
          kind: "entity",
          values: [
            { id: "q0", kind: "qubit", metadata: { label: "Q0" } },
            { id: "q1", kind: "qubit", metadata: { label: "Q1" } },
          ],
        },
      },
      schema.dimensions[1]!,
    ],
    variables: schema.variables?.map((variable) =>
      variable.id === "frequency" || variable.id === "response"
        ? {
            ...variable,
            dims: ["point", "qubit", "sample"],
            source_entity_products: {
              dimension_id: "qubit",
              product_ids: ["q0-readout", "q1-readout"],
            },
            ...(variable.id === "response"
              ? {
                  entity_acquisition: {
                    policy: "all_or_nothing" as const,
                    cohort_id: "readout-cohort",
                  },
                }
              : {}),
          }
        : variable,
    ),
  };
}

function traceAxis(id: string, values: number[]) {
  return {
    id,
    size: values.length,
    source: {
      kind: "values" as const,
      values: values.map((value) => ({
        kind: "scalar" as const,
        dtype: "float64" as const,
        value,
      })),
    },
  };
}

function tracePreview(mode: "imag" | "magnitude" | "phase" | "real" | "value") {
  return {
    coordinate_id: "frequency",
    coordinate_label: "Frequency",
    coordinate_unit: "GHz",
    dimension_id: "sample",
    downsampling: "minmax" as const,
    layout: "overlay" as const,
    fixed_axis_indices: { bias: 0 },
    observable_id: "response",
    observable_label: "S21",
    observable_unit: "ratio",
    recording_group_id: "readout",
    returned_sample_count: 3,
    returned_series_count: 1,
    inspected_series_count: 4,
    failures: [],
    samples_reduced: false,
    selected_series_count: 4,
    series: [
      {
        label: "Point 0",
        logical_point_id: "point-0",
        point_index: 0,
        source_sample_count: 3,
        available_sample_count: 3,
        unavailable_reasons: [],
        x: [4.9, 5, 5.1],
        y: [0.1, 0.2, 0.3],
      },
    ],
    source_sample_count: 3,
    truncated_series: false,
    value_mode: mode,
    value_unit: mode === "phase" ? "rad" : "ratio",
  };
}

function emitProjectEvent(runId: string, kind: string): void {
  if (!projectEventListener) throw new Error("project SSE listener is not ready");
  projectEventListener(
    new MessageEvent("project", {
      data: JSON.stringify({ event_id: 42, run_id: runId, kind, payload: {} }),
    }),
  );
}

function emitSseOpen(): void {
  if (!openEventListener) throw new Error("SSE open listener is not ready");
  openEventListener(new Event("open"));
}

function canonicalQueryCallCounts(): number[] {
  return [
    vi.mocked(getRuns).mock.calls.length,
    vi.mocked(getEvents).mock.calls.length,
    vi.mocked(getRun).mock.calls.length,
    vi.mocked(getRunEvents).mock.calls.length,
    vi.mocked(getMeasurementPreview).mock.calls.length,
    vi.mocked(getRunAnalysisSummaries).mock.calls.length,
  ];
}

it("retains the calibration route and procedure when navigating back", async () => {
  window.history.replaceState(null, "", "/?procedure=p1#configuration");
  renderApp();
  fireEvent.click(await screen.findByRole("button", { name: "Experiments" }));
  expect(await screen.findByText("Calibration launcher")).toBeVisible();
  expect(window.location.hash).toBe("#launch");
  expect(new URLSearchParams(window.location.search).get("procedure")).toBe("p1");
});
