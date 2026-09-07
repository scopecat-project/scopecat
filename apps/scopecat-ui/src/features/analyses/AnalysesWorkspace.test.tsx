// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectAnalysis, ProjectAnalysisSummary } from "../../types";
import { AnalysesWorkspace } from "./AnalysesWorkspace";
import {
  getOlderProjectAnalysisSummaries,
  getProjectAnalysis,
  getProjectAnalysisSummaries,
} from "./analysis-api";

vi.mock("./analysis-api", () => ({
  getProjectAnalysis: vi.fn(),
  getProjectAnalysisSummaries: vi.fn(),
  getOlderProjectAnalysisSummaries: vi.fn(),
  getProjectAnalysisArtifactDownload: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(getProjectAnalysisSummaries).mockResolvedValue({
    items: [projectAnalysisSummary()],
  });
  vi.mocked(getOlderProjectAnalysisSummaries).mockResolvedValue({ items: [] });
  vi.mocked(getProjectAnalysis).mockResolvedValue(projectAnalysis());
});

afterEach(() => {
  cleanup();
});

describe("AnalysesWorkspace", () => {
  it("shows cross-run provenance and opens an exact input run", async () => {
    const openRun = vi.fn();
    renderWorkspace(openRun);

    expect(await screen.findByRole("heading", { name: "Project analyses" })).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Candidate verification" })).toBeVisible();
    expect(screen.getByText("Revision 2")).toBeVisible();
    expect(screen.getByText("sha256:publication")).toHaveAttribute("title", "sha256:publication");
    expect(screen.getByText("verification.v1")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "run-candidate" }));

    expect(openRun).toHaveBeenCalledWith("run-candidate");
  });

  it("shows exact procedure decision provenance without treating it as a run", async () => {
    const analysis = projectAnalysis();
    analysis.inputs = [
      {
        id: "selection",
        kind: "interpretation",
        target: "select-gap",
        content_hash: "sha256:response",
        codec: "scopecat.interpretation-response.v1",
        role: "decision",
        source: {
          procedure_run_id: "procedure-test",
          step_key: "select-gap",
          request_hash: "sha256:request",
          response_hash: "sha256:response",
        },
      },
    ];
    vi.mocked(getProjectAnalysis).mockResolvedValue(analysis);
    renderWorkspace(vi.fn());
    expect(await screen.findByText("procedure-test:select-gap")).toHaveAttribute(
      "title",
      "sha256:response",
    );
    expect(screen.getByText("Procedure decision")).toBeVisible();
    expect(screen.queryByRole("button", { name: "procedure-test" })).toBeNull();
  });

  it("explains how to create the first project analysis", async () => {
    vi.mocked(getProjectAnalysisSummaries).mockResolvedValue({ items: [] });

    renderWorkspace(vi.fn());

    expect(await screen.findByText("No project analyses saved")).toBeVisible();
    expect(screen.getByText(/lab\.analyze\(step\)/)).toBeVisible();
  });

  it("loads older project analyses through the page cursor", async () => {
    vi.mocked(getProjectAnalysisSummaries).mockResolvedValue({
      items: [projectAnalysisSummary()],
      nextCursor: 17,
    });
    vi.mocked(getOlderProjectAnalysisSummaries).mockResolvedValue({
      items: [projectAnalysisSummary("analysis-older-r1", "Older verification", 1)],
    });

    renderWorkspace(vi.fn());

    fireEvent.click(await screen.findByRole("button", { name: "Load older analyses" }));

    expect(await screen.findByText("Older verification")).toBeVisible();
    expect(getOlderProjectAnalysisSummaries).toHaveBeenCalledWith(17, expect.any(AbortSignal));
  });
});

function renderWorkspace(onOpenRun: (runId: string) => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AnalysesWorkspace
        daemonUnavailable={false}
        onOpenRun={onOpenRun}
        onSelectAnalysis={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

function projectAnalysisSummary(
  id = "analysis-candidate-verification-r2",
  title = "Candidate verification",
  revision = 2,
): ProjectAnalysisSummary {
  return {
    id,
    title,
    key: "candidate-verification",
    stepId: "candidate-verification",
    revision,
    publicationHash: "sha256:publication",
    publishedAt: "2026-08-17T12:00:00Z",
    inputCount: 1,
    outputCount: 1,
  };
}

function projectAnalysis(): ProjectAnalysis {
  return {
    id: "analysis-candidate-verification-r2",
    title: "Candidate verification",
    key: "candidate-verification",
    stepId: "candidate-verification",
    revision: 2,
    publicationHash: "sha256:publication",
    publishedAt: "2026-08-17T12:00:00Z",
    subject: "project",
    inputs: [
      {
        id: "candidate",
        run_id: "run-candidate",
        target: "datasets/raw-measurements",
        kind: "measurement_dataset",
        content_hash: "sha256:measurements",
        codec: "scopecat.measurements.arrow.v1",
        role: "candidate",
      },
    ],
    executions: [],
    outputs: [
      {
        kind: "fact",
        id: "decision",
        title: "Decision",
        content: {
          schema_id: "verification.v1",
          schema_codec: "scopecat.analysis-fact-schema.v1",
          schema_hash: "sha256:schema",
          codec: "scopecat.python-json.v1",
          value: { accepted: true },
        },
        metadata: {},
      },
    ],
  };
}
