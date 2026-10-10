// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getOlderRunParameterProposals,
  getRunParameterProposals,
} from "../../data/parameter-proposals/api";
import type {
  ParameterProposal,
  RunParameterProposalPage,
} from "../../data/parameter-proposals/types";
import { RunProposals } from "./RunProposals";

vi.mock("../../data/parameter-proposals/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../data/parameter-proposals/api")>();
  return {
    ...original,
    getOlderRunParameterProposals: vi.fn(),
    getRunParameterProposals: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RunProposals", () => {
  it("shows candidate evidence and an explicit VS Code trial without a global default action", async () => {
    vi.mocked(getRunParameterProposals).mockResolvedValue(
      proposalList(approvedProposal(), pendingProposal({ id: "new-fit" })),
    );
    renderProposals();

    expect(await screen.findByText("Approval recorded")).toBeVisible();
    expect(screen.getAllByText("selected-fit")).toHaveLength(2);
    expect(screen.getAllByText("Verify and adopt in an author session")).toHaveLength(2);
    expect(screen.getByText(/session.config.candidate\("run-1", "new-fit"\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept as default" })).not.toBeInTheDocument();
  });

  it("shows exact changed cells and the explicit verification and publication boundary", async () => {
    const longValue = "retained-value-".repeat(12);
    vi.mocked(getRunParameterProposals).mockResolvedValue(
      proposalList(
        pendingProposal({
          deltas: [
            {
              parameterId: "drive",
              before: [
                { qubit: "q0", frequency: 5 },
                { qubit: "q1", frequency: 6 },
              ],
              after: [
                { qubit: "q0", frequency: 5.1 },
                { qubit: "q1", frequency: 6 },
              ],
              cells: [
                {
                  key: { qubit: "q0" },
                  field: "frequency",
                  before: 5,
                  after: 5.1,
                  change_kind: "physical",
                },
                { key: { qubit: "q0" }, field: "label", after: longValue, change_kind: "added" },
              ],
            },
          ],
        }),
      ),
    );
    renderProposals();
    expect(await screen.findByText("drive[qubit=q0].frequency")).toBeVisible();
    expect(screen.getByText("drive[qubit=q0].label")).toBeVisible();
    expect(screen.getByText(longValue)).toBeVisible();
    expect(screen.queryByText(/q1/)).toBeNull();
    fireEvent.click(screen.getByText("Verify and adopt in an author session"));
    expect(screen.getByText("verified = candidate.verify(check_result)")).toBeVisible();
    expect(
      screen.getByText(/print\(baseline_branch.name, baseline_branch.revision\)/),
    ).toBeVisible();
    expect(screen.getByText(/This advances only the chosen branch/)).toBeVisible();
    expect(
      screen.getByText("session.use(parameters=published.revision, setup=setup)"),
    ).toBeVisible();
  });

  it("distinguishes an empty cell diff from an atomic value change", async () => {
    vi.mocked(getRunParameterProposals).mockResolvedValue(
      proposalList(
        pendingProposal({
          deltas: [
            { parameterId: "reordered-table", before: [1, 2], after: [2, 1], cells: [] },
            { parameterId: "scalar", before: 5, after: 6 },
          ],
        }),
      ),
    );
    renderProposals();
    expect(await screen.findByText("No changed keyed cells")).toBeVisible();
    expect(screen.getByText("[1,2]")).toBeVisible();
    expect(screen.getByText("[2,1]")).toBeVisible();
    expect(screen.getByText("scalar")).toBeVisible();
    expect(screen.getByText("5")).toBeVisible();
    expect(screen.getByText("6")).toBeVisible();
  });

  it("loads older proposal pages explicitly", async () => {
    vi.mocked(getRunParameterProposals).mockResolvedValue({
      ...proposalList(pendingProposal({ id: "latest-proposal" })),
      nextCursor: 17,
    });
    vi.mocked(getOlderRunParameterProposals).mockResolvedValue(
      proposalList(pendingProposal({ id: "older-proposal" })),
    );
    renderProposals();

    fireEvent.click(await screen.findByRole("button", { name: "Load older proposals" }));

    expect(await screen.findByText("older-proposal")).toBeVisible();
    expect(getOlderRunParameterProposals).toHaveBeenCalledWith(
      "run-1",
      17,
      expect.any(AbortSignal),
    );
  });

  it("links each candidate to its exact source run and analysis, including older publications", async () => {
    vi.mocked(getRunParameterProposals).mockResolvedValue(
      proposalList(
        pendingProposal({ sourceRunId: "run/a & b", analysisRecordId: "fit/older#1" }),
        pendingProposal({ id: "new-fit", analysisRecordId: "fit-newer" }),
      ),
    );
    renderProposals();

    const links = await screen.findAllByRole("link", { name: "View source analysis" });
    expect(links[0]).toHaveAttribute(
      "href",
      "?run=run%2Fa%20%26%20b&run-analysis=fit%2Folder%231#runs",
    );
    expect(links[1]).toHaveAttribute("href", "?run=run-1&run-analysis=fit-newer#runs");
  });
});

function renderProposals() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RunProposals runId="run-1" />
    </QueryClientProvider>,
  );
}

function proposalList(...items: ParameterProposal[]): RunParameterProposalPage {
  return { runId: "run-1", items };
}

function pendingProposal(overrides: Partial<ParameterProposal> = {}): ParameterProposal {
  return {
    id: "drive-frequency",
    sourceRunId: "run-1",
    analysisRecordId: "analysis-fit-r1",
    baseConfigId: "baseline",
    baseContentHash: "sha256:base",
    reason: "Peak moved",
    evidenceOutputIds: ["selected-fit"],
    confidence: 0.94,
    proposedAt: "2026-07-23T10:00:00Z",
    deltas: [
      {
        parameterId: "q0.drive.frequency",
        before: { value: 5, unit: "GHz" },
        after: { value: 5.1, unit: "GHz" },
      },
    ],
    ...overrides,
  };
}

function approvedProposal(overrides: Partial<ParameterProposal> = {}): ParameterProposal {
  const proposal = pendingProposal(overrides);
  return {
    ...proposal,
    approval: {
      actor: "Ada",
      note: "Verified",
      approvedAt: proposal.proposedAt,
    },
  };
}
