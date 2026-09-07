// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { getRunAnalysis } from "../runs/run-api";
import { getProjectAnalysis } from "../analyses/analysis-api";
import {
  getRunParameterProposals,
  getOlderRunParameterProposals,
} from "../../data/parameter-proposals/api";
import { DecisionEvidence } from "./DecisionEvidence";
import type { RunAnalysis } from "../../types";

vi.mock("../runs/run-api", () => ({ getRunAnalysis: vi.fn(), getRunArtifactDownload: vi.fn() }));
vi.mock("../analyses/analysis-api", () => ({
  getProjectAnalysis: vi.fn(),
  getProjectAnalysisArtifactDownload: vi.fn(),
}));
vi.mock("../../data/parameter-proposals/api", () => ({
  getRunParameterProposals: vi.fn(),
  getOlderRunParameterProposals: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const publication: RunAnalysis = {
  id: "fit-r1",
  title: "Baseline fit",
  subject: "run",
  revision: 1,
  publicationHash: "sha256:abc",
  publishedAt: "2026-09-07",
  inputs: [],
  executions: [],
  outputs: [
    {
      id: "proposal",
      title: "Candidate",
      metadata: {},
      kind: "parameter_change_proposal",
      content: { proposal_id: "p1", record_ref: "p1" },
    },
  ],
};

it("shows changes from the exact retained analysis, including older proposal pages", async () => {
  vi.mocked(getRunAnalysis).mockResolvedValue(publication);
  vi.mocked(getRunParameterProposals).mockResolvedValue({
    runId: "run-1",
    items: [],
    nextCursor: 4,
  });
  vi.mocked(getOlderRunParameterProposals).mockResolvedValue({
    runId: "run-1",
    items: [
      {
        id: "p1",
        sourceRunId: "run-1",
        analysisRecordId: "fit-r1",
        baseConfigId: "base",
        baseContentHash: "hash",
        reason: "Calibrated amplitude",
        evidenceOutputIds: ["fit"],
        deltas: [{ parameterId: "drive_pi_amplitude", before: 0.1596, after: 0.1605 }],
      },
    ],
  });
  renderEvidence({
    kind: "analysis",
    analysis_record_id: "fit-r1",
    subject: { kind: "run", run_id: "run-1" },
  });
  expect(await screen.findByRole("table", { name: "Proposed parameter changes" })).toBeVisible();
  expect(screen.getByText("0.1596")).toBeVisible();
  expect(screen.getByText("0.1605")).toBeVisible();
  expect(getRunAnalysis).toHaveBeenCalledWith("run-1", "fit-r1", expect.any(AbortSignal));
  expect(getOlderRunParameterProposals).toHaveBeenCalledWith("run-1", 4, expect.any(AbortSignal));
});

it("reports missing project evidence instead of showing unrelated results", async () => {
  vi.mocked(getProjectAnalysis).mockRejectedValue(new Error("Missing retained publication"));
  renderEvidence({
    kind: "analysis",
    analysis_record_id: "verification-r1",
    subject: { kind: "project" },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("Missing retained publication");
  expect(getProjectAnalysis).toHaveBeenCalledWith("verification-r1", expect.any(AbortSignal));
  expect(getRunAnalysis).not.toHaveBeenCalled();
});

function renderEvidence(input: Parameters<typeof DecisionEvidence>[0]["input"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DecisionEvidence input={input} />
    </QueryClientProvider>,
  );
}
