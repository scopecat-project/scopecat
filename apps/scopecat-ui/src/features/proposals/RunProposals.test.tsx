// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getConfigRegistry } from "../config/config-api";
import {
  acceptProposal,
  getOlderRunParameterProposals,
  getRunParameterProposals,
} from "../../data/parameter-proposals/api";
import type {
  ParameterProposal,
  RunParameterProposalPage,
} from "../../data/parameter-proposals/types";
import { RunProposals } from "./RunProposals";

vi.mock("../config/config-api", () => ({
  getConfigRegistry: vi.fn(),
}));

vi.mock("../../data/parameter-proposals/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../data/parameter-proposals/api")>();
  return {
    ...original,
    acceptProposal: vi.fn(),
    getOlderRunParameterProposals: vi.fn(),
    getRunParameterProposals: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RunProposals", () => {
  it("can accept each approved proposal as the default without exposing generations", async () => {
    const older = approvedProposal({
      id: "older-proposal",
      proposedAt: "2026-07-22T10:00:00Z",
    });
    const latest = approvedProposal({
      id: "latest-proposal",
      proposedAt: "2026-07-23T10:00:00Z",
    });
    vi.mocked(getRunParameterProposals).mockResolvedValue(proposalList(older, latest));
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: configActivation(),
      activation_history: [],
      entries: [],
    });
    vi.mocked(acceptProposal).mockResolvedValue();
    renderProposals();

    const setDefault = await screen.findAllByRole("button", {
      name: "Accept as default",
    });
    expect(screen.getAllByTestId("proposal-state")).toHaveLength(2);
    expect(screen.getAllByText("selected-fit")).toHaveLength(2);
    expect(setDefault).toHaveLength(2);
    await waitFor(() => expect(setDefault[1]).toBeEnabled());
    fireEvent.click(setDefault[1]!);
    const defaultDialog = await screen.findByRole("alertdialog");
    expect(defaultDialog).toHaveTextContent(
      "Accept proposal latest-proposal and set its configuration as the default.",
    );
    fireEvent.click(within(defaultDialog).getByRole("button", { name: "Accept as default" }));

    await waitFor(() =>
      expect(acceptProposal).toHaveBeenCalledWith({
        runId: "run-1",
        proposalId: "latest-proposal",
        actor: "local-operator",
        expectedGeneration: 3,
        note: "",
      }),
    );
  });

  it("accepts a pending proposal in one action and reports publish failure", async () => {
    vi.mocked(getRunParameterProposals).mockResolvedValue(proposalList(pendingProposal()));
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: configActivation(),
      activation_history: [],
      entries: [],
    });
    vi.mocked(acceptProposal).mockRejectedValue(new Error("generation conflict"));
    renderProposals();

    const accept = await screen.findByRole("button", {
      name: "Accept as default",
    });
    await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.click(accept);
    const defaultDialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(defaultDialog).getByRole("button", { name: "Accept as default" }));

    expect(await screen.findByText("generation conflict")).toBeInTheDocument();
  });

  it("loads older proposal pages explicitly", async () => {
    vi.mocked(getRunParameterProposals).mockResolvedValue({
      ...proposalList(pendingProposal({ id: "latest-proposal" })),
      nextCursor: 17,
    });
    vi.mocked(getOlderRunParameterProposals).mockResolvedValue(
      proposalList(pendingProposal({ id: "older-proposal" })),
    );
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: configActivation(),
      activation_history: [],
      entries: [],
    });
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

function configActivation() {
  return {
    generation: 3,
    action: "activation" as const,
    entry_id: "baseline",
    entry_content_hash: "sha256:base",
    actor: "Ada",
    note: "",
    recorded_at: "2026-07-24T08:00:00Z",
  };
}

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
