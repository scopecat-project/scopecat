// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SampleSummary, SampleView } from "../../api-contract";
import type { ProjectRun } from "../../types";
import { SamplesWorkspace } from "./SamplesWorkspace";
import {
  getSample,
  getSampleAnalyses,
  getSampleAnalysis,
  getSampleRevision,
  getSampleRevisions,
  getSampleRuns,
  getSamples,
} from "./sample-api";

vi.mock("./sample-api", () => ({
  getSample: vi.fn(),
  getSampleAnalyses: vi.fn(),
  getSampleAnalysis: vi.fn(),
  getSampleRevision: vi.fn(),
  getSampleRevisions: vi.fn(),
  getSampleRuns: vi.fn(),
  getSamples: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(getSamples).mockResolvedValue({ items: [sampleSummary()] });
  vi.mocked(getSample).mockResolvedValue(sampleView());
  vi.mocked(getSampleRevisions).mockResolvedValue({
    sample_id: "chip-a17",
    items: [sampleView().revision],
  });
  vi.mocked(getSampleRevision).mockResolvedValue(sampleView().revision);
  vi.mocked(getSampleRuns).mockResolvedValue({ items: [sampleRun()] });
  vi.mocked(getSampleAnalyses).mockResolvedValue({ sample_id: "chip-a17", items: [] });
  vi.mocked(getSampleAnalysis).mockRejectedValue(new Error("not selected"));
});

afterEach(() => {
  cleanup();
});

describe("SamplesWorkspace", () => {
  it("shows topology, exact history, and opens a related run", async () => {
    const openRun = vi.fn();
    renderWorkspace({ selectedSampleId: "chip-a17", onOpenRun: openRun });

    expect(await screen.findByRole("heading", { name: "Chip A17" })).toBeVisible();
    expect(screen.getByRole("img", { name: "Sample topology map" })).toBeVisible();
    expect(screen.getByText("mask:g4-r3")).toBeVisible();
    expect(screen.getByText("Revision 2")).toBeVisible();

    fireEvent.click(screen.getByText("q0", { selector: "text" }));

    expect(screen.getByRole("heading", { name: "Selected entity" })).toBeVisible();
    expect(screen.getByText("1", { selector: "dd" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: /Ramsey calibration/ }));
    expect(openRun).toHaveBeenCalledWith("run-ramsey");
  });

  it("filters the registry by sample metadata", async () => {
    renderWorkspace();

    expect(await screen.findByText("Chip A17")).toBeVisible();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search samples" }), {
      target: { value: "unrelated" },
    });

    expect(screen.getByText("No matching samples")).toBeVisible();
  });

  it("opens an exact historical revision and returns to active state", async () => {
    const onSelectSample = vi.fn();
    vi.mocked(getSampleRevision).mockResolvedValue({
      ...sampleView().revision,
      revision: 1,
      content_hash: `sha256:${"b".repeat(64)}`,
      content: {
        ...sampleView().revision.content,
        display_name: "Chip A17 at registration",
        status: "available",
      },
    });

    renderWorkspace({
      selectedSampleId: "chip-a17",
      selectedSampleRevision: 1,
      onSelectSample,
    });

    expect(await screen.findByRole("heading", { name: "Chip A17 at registration" })).toBeVisible();
    expect(screen.getByText(/Viewing historical revision 1/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "View active revision" }));
    expect(onSelectSample).toHaveBeenCalledWith("chip-a17");
  });
});

function renderWorkspace({
  selectedSampleId,
  selectedSampleRevision,
  onSelectSample = vi.fn(),
  onOpenRun = vi.fn(),
}: {
  selectedSampleId?: string;
  selectedSampleRevision?: number;
  onSelectSample?: (sampleId: string, revision?: number) => void;
  onOpenRun?: (runId: string) => void;
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SamplesWorkspace
        selectedSampleId={selectedSampleId}
        selectedSampleRevision={selectedSampleRevision}
        onSelectSample={onSelectSample}
        onOpenRun={onOpenRun}
        daemonUnavailable={false}
      />
    </QueryClientProvider>,
  );
}

function sampleSummary(): SampleSummary {
  return sampleView();
}

function sampleView(): SampleView {
  const revision = {
    sample_id: "chip-a17",
    revision: 2,
    content_hash: `sha256:${"a".repeat(64)}`,
    recorded_at: "2026-08-25T08:00:00Z",
    actor: "operator",
    note: "Mounted in fridge 2",
    content: {
      display_name: "Chip A17",
      status: "mounted" as const,
      design_ref: "mask:g4-r3",
      aliases: ["A17"],
      tags: ["fridge-2", "generation-4"],
      relations: [{ kind: "diced-from", sample_id: "wafer-4" }],
      properties: { cooldown: 3 },
      topology: {
        entities: [
          { id: "q0", kind: "qubit", metadata: {} },
          { id: "q1", kind: "qubit", metadata: {} },
        ],
        connections: [
          {
            id: "q0-q1",
            kind: "coupling",
            endpoints: ["q0", "q1"],
          },
        ],
      },
      geometry: {
        kind: "cartesian" as const,
        unit: "mm",
        width: 10,
        height: 10,
        points: [
          { entity_id: "q0", x: 3, y: 5 },
          { entity_id: "q1", x: 7, y: 5 },
        ],
      },
      artifacts: [],
    },
  };
  return {
    record: {
      id: "chip-a17",
      kind: "chip",
      active_revision: 2,
      created_at: "2026-08-20T08:00:00Z",
    },
    revision,
    run_count: 1,
    last_run_at: "2026-08-25T10:00:00Z",
  };
}

function sampleRun(): ProjectRun {
  return {
    runId: "run-ramsey",
    experimentId: "ramsey",
    displayName: "Ramsey calibration",
    tags: [],
    status: "succeeded",
    stateLabel: "Succeeded",
    updatedAt: "2026-08-25T10:00:00Z",
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
    resources: [],
    samples: [
      {
        role: "subject",
        sample_id: "chip-a17",
        revision: 2,
        content_hash: `sha256:${"a".repeat(64)}`,
        kind: "chip",
        display_name: "Chip A17",
      },
    ],
    contents: [],
  };
}
