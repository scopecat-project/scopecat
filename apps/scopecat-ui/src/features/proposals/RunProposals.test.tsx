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
    expect(screen.getAllByText("Try this candidate in VS Code")).toHaveLength(2);
    expect(screen.getByText(/session.config.candidate\("run-1", "new-fit"\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept as default" })).not.toBeInTheDocument();
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
