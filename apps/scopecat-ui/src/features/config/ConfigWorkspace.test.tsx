// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getRunAnalysis } from "../runs/run-api";
import {
  activateConfigEntry,
  getConfigRegistry,
  getConfigRegistryEntry,
  getOlderConfigActivationHistory,
  getOlderConfigRegistryEntries,
} from "./config-api";
import { ConfigWorkspace } from "./ConfigWorkspace";
import type { ConfigProfileSnapshot, ConfigRegistryEntry } from "../../api-contract";
import { getRunParameterProposals } from "../../data/parameter-proposals/api";

vi.mock("../runs/run-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../runs/run-api")>()),
  getRunAnalysis: vi.fn(),
}));

vi.mock("./config-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./config-api")>()),
  activateConfigEntry: vi.fn(),
  getConfigRegistry: vi.fn(),
  getConfigRegistryEntry: vi.fn(),
  getOlderConfigActivationHistory: vi.fn(),
  getOlderConfigRegistryEntries: vi.fn(),
}));

vi.mock("../../data/parameter-proposals/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../data/parameter-proposals/api")>()),
  getRunParameterProposals: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(getRunParameterProposals).mockResolvedValue({
    runId: "run-calibration",
    items: [],
  });
  vi.mocked(getRunAnalysis).mockRejectedValue(new Error("analysis unavailable"));
  vi.mocked(getOlderConfigRegistryEntries).mockResolvedValue({ entries: [] });
  vi.mocked(getOlderConfigActivationHistory).mockResolvedValue({ items: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ConfigWorkspace", () => {
  it("pins an old active entry even when it is outside the registry head page", async () => {
    const current = configEntry("old-active", "sha256:old-active");
    const recent = configEntry("recent", "sha256:recent");
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(120, current.id, current.content_hash),
      activation_history: [activation(120, current.id, current.content_hash)],
      entries: [recent],
      entries_next_cursor: 119,
    });
    vi.mocked(getConfigRegistryEntry).mockImplementation(async (entryId) =>
      entryDetail(entryId === current.id ? current : recent),
    );

    renderWorkspace();

    expect(await screen.findByRole("button", { name: /old-active/i })).toBeInTheDocument();
    expect(screen.getAllByText("Default").length).toBeGreaterThan(0);
    expect(getConfigRegistryEntry).toHaveBeenCalledWith("old-active", expect.any(AbortSignal));
  });

  it("loads older saved versions and activation history independently", async () => {
    const current = configEntry("current", "sha256:current");
    const older = configEntry("older", "sha256:older");
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(3, current.id, current.content_hash),
      activation_history: [activation(3, current.id, current.content_hash)],
      entries: [current],
      entries_next_cursor: 2,
      activation_history_next_cursor: 3,
    });
    vi.mocked(getOlderConfigRegistryEntries).mockResolvedValue({
      entries: [older],
      activation: activation(3, current.id, current.content_hash),
    });
    vi.mocked(getOlderConfigActivationHistory).mockResolvedValue({
      items: [activation(2, older.id, older.content_hash)],
    });
    vi.mocked(getConfigRegistryEntry).mockImplementation(async (entryId) =>
      entryDetail(entryId === older.id ? older : current),
    );

    renderWorkspace();

    expect(await screen.findByRole("button", { name: "Undo" })).toBeDisabled();
    fireEvent.click(await screen.findByRole("button", { name: "Load older versions" }));
    expect(await screen.findByText("older")).toBeInTheDocument();
    expect(getOlderConfigRegistryEntries).toHaveBeenCalledWith(2);

    fireEvent.click(screen.getByRole("button", { name: "Load older changes" }));
    expect(await screen.findByText("G2")).toBeInTheDocument();
    expect(getOlderConfigActivationHistory).toHaveBeenCalledWith(3);
    expect(screen.getByRole("button", { name: "Undo" })).toBeEnabled();
  });

  it("presents saved versions as defaults and undo without generation ceremony", async () => {
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(3, "baseline", "sha256:baseline"),
      activation_history: [
        activation(3, "baseline", "sha256:baseline", "baseline"),
        activation(2, "baseline", "sha256:baseline", "calibrated"),
        activation(1, "calibrated", "sha256:calibrated", undefined, "inventory_migration"),
      ],
      entries: [
        configEntry("baseline", "sha256:baseline"),
        configEntry("calibrated", "sha256:calibrated"),
      ],
    });
    vi.mocked(getConfigRegistryEntry).mockImplementation(async (entryId) => {
      const contentHash = `sha256:${entryId}`;
      return {
        entry: configEntry(entryId, contentHash),
        config: emptyConfig(entryId),
        summary: {
          id: entryId,
          primaryEntityId: "q0",
          parameterCount: 0,
          instrumentCount: 0,
        },
      };
    });
    vi.mocked(activateConfigEntry).mockResolvedValue();

    renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "Default configuration" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Saved versions").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Undo" })).toBeEnabled();
    expect(screen.queryByText("Runtime-derived default")).not.toBeInTheDocument();
    expect(await screen.findByText("Direct configuration profile")).toBeInTheDocument();
    expect(screen.getByText("Saved from one complete config snapshot.")).toBeInTheDocument();
    expect(screen.queryByText(/application-provided/i)).not.toBeInTheDocument();
    expect(screen.getByText("Inventory migration")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /calibrated.*direct profile/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Set as default" }));
    const activateDialog = await screen.findByRole("alertdialog");
    expect(activateDialog).toHaveTextContent("Set calibrated as the default configuration?");
    fireEvent.click(within(activateDialog).getByRole("button", { name: "Set as default" }));

    await waitFor(() =>
      expect(activateConfigEntry).toHaveBeenCalledWith({
        operation_id: expect.stringMatching(/^ui-config-activate-/),
        entry_id: "calibrated",
        actor: "local-operator",
        note: "",
        expected_generation: 3,
      }),
    );

    const undo = screen.getByRole("button", { name: "Undo" });
    await waitFor(() => expect(undo).toBeEnabled());
    fireEvent.click(undo);
    const undoDialog = await screen.findByRole("alertdialog");
    expect(undoDialog).toHaveTextContent("Restore calibrated as the default configuration?");
    fireEvent.click(within(undoDialog).getByRole("button", { name: "Restore default" }));
    await waitFor(() =>
      expect(activateConfigEntry).toHaveBeenLastCalledWith({
        operation_id: expect.stringMatching(/^ui-config-undo-/),
        entry_id: "calibrated",
        actor: "local-operator",
        note: "",
        expected_generation: 3,
      }),
    );
  });

  it.each(["manual_parameter_updates", "candidate_config", "calibration_cohort_merge"] as const)(
    "marks a %s default as runtime-derived without claiming source drift",
    async (sourceKind) => {
      const entry = runtimeDerivedEntry(sourceKind);
      vi.mocked(getConfigRegistry).mockResolvedValue({
        activation: activation(3, entry.id, entry.content_hash),
        activation_history: [activation(3, entry.id, entry.content_hash)],
        entries: [entry],
      });
      vi.mocked(getConfigRegistryEntry).mockResolvedValue({
        entry,
        config: emptyConfig(entry.id),
        summary: {
          id: entry.id,
          primaryEntityId: "q0",
          parameterCount: 0,
          instrumentCount: 0,
        },
      });

      renderWorkspace();

      const notice = await screen.findByRole("note");
      expect(within(notice).getByText("Runtime-derived default")).toBeInTheDocument();
      expect(notice).toHaveTextContent(
        "cannot tell whether the project's Git/Python configuration source is synchronized",
      );
      expect(notice).toHaveTextContent("scopecat config diff .");
      expect(notice).not.toHaveTextContent(/drift/i);
    },
  );

  it("opens the immutable base entry for a manual parameter update", async () => {
    const entry = runtimeDerivedEntry("manual_parameter_updates");
    const baseline = configEntry("baseline", "sha256:baseline");
    const entries = [entry, baseline];
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(3, entry.id, entry.content_hash),
      activation_history: [activation(3, entry.id, entry.content_hash)],
      entries,
    });
    vi.mocked(getConfigRegistryEntry).mockImplementation(async (entryId) => {
      const selected = entries.find((item) => item.id === entryId)!;
      return entryDetail(selected);
    });

    renderWorkspace();

    expect(await screen.findByText("Manual parameter update")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open base version baseline" }));

    expect(await screen.findByRole("heading", { name: "baseline" })).toBeInTheDocument();
    expect(screen.getByText("Direct configuration profile")).toBeInTheDocument();
  });

  it("renders calibration cohort provenance without treating it as one candidate", async () => {
    const entry = runtimeDerivedEntry("calibration_cohort_merge");
    const baseline = configEntry("baseline", "sha256:baseline");
    const entries = [entry, baseline];
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(3, entry.id, entry.content_hash),
      activation_history: [activation(3, entry.id, entry.content_hash)],
      entries,
    });
    vi.mocked(getConfigRegistryEntry).mockImplementation(async (entryId) => {
      const selected = entries.find((item) => item.id === entryId)!;
      return entryDetail(selected);
    });
    const openRun = vi.fn();

    renderWorkspace(openRun);

    expect(await screen.findByText("Verified calibration cohort")).toBeInTheDocument();
    expect(screen.getAllByText("Calibration cohort merge").length).toBeGreaterThan(0);
    expect(screen.queryByText("Analysis candidate")).not.toBeInTheDocument();
    expect(screen.getByText("drag-nightly-2026-07-24")).toBeInTheDocument();
    expect(screen.getByText("reference_lab.drag-composition@1")).toBeInTheDocument();
    const contribution = screen.getByRole("article", { name: "Calibration member q0" });
    expect(contribution).toHaveTextContent("verified_parameter_proposal_v1");
    expect(contribution).toHaveTextContent("procedure-drag-q0");
    expect(contribution).toHaveTextContent("verification#1");
    expect(contribution).toHaveTextContent("run-baseline-q0");
    expect(contribution).toHaveTextContent("analysis-fit-q0");
    expect(contribution).toHaveTextContent("proposal-q0");
    expect(contribution).toHaveTextContent("run-candidate-q0");
    expect(contribution).toHaveTextContent("analysis-verify-q0 · decision");
    fireEvent.click(screen.getByRole("button", { name: "Open candidate run" }));
    expect(openRun).toHaveBeenCalledWith("run-candidate-q0");

    fireEvent.click(screen.getByRole("button", { name: "Open base version baseline" }));
    expect(await screen.findByRole("heading", { name: "baseline" })).toBeInTheDocument();
  });

  it("follows a refreshed default until the operator selects a saved version", async () => {
    const baseline = configEntry("baseline", "sha256:baseline");
    const candidate = runtimeDerivedEntry("candidate_config");
    const imported = configEntry("imported", "sha256:imported");
    const initial = {
      activation: activation(1, baseline.id, baseline.content_hash),
      activation_history: [activation(1, baseline.id, baseline.content_hash)],
      entries: [baseline],
    };
    const accepted = {
      activation: activation(2, candidate.id, candidate.content_hash),
      activation_history: [
        activation(2, candidate.id, candidate.content_hash),
        ...initial.activation_history,
      ],
      entries: [candidate, baseline],
    };
    const importedDefault = {
      activation: activation(3, imported.id, imported.content_hash),
      activation_history: [
        activation(3, imported.id, imported.content_hash),
        ...accepted.activation_history,
      ],
      entries: [imported, ...accepted.entries],
    };
    vi.mocked(getConfigRegistry)
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(accepted)
      .mockResolvedValue(importedDefault);
    vi.mocked(getConfigRegistryEntry).mockImplementation(async (entryId) => {
      const entry = importedDefault.entries.find((item) => item.id === entryId)!;
      return entryDetail(entry);
    });

    const { queryClient } = renderWorkspace();

    expect(await screen.findByRole("heading", { name: baseline.id })).toBeInTheDocument();
    await queryClient.invalidateQueries({ queryKey: ["config", "registry"] });
    expect(await screen.findByText("Analysis candidate")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /baseline.*direct profile/i }));
    expect(await screen.findByRole("heading", { name: baseline.id })).toBeInTheDocument();
    await queryClient.invalidateQueries({ queryKey: ["config", "registry"] });
    expect(screen.getByRole("heading", { name: baseline.id })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("active-config-entry")).toHaveTextContent(imported.id),
    );
  });

  it("joins candidate proposals, analyses, and approval", async () => {
    const entry = runtimeDerivedEntry("candidate_config");
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(3, entry.id, entry.content_hash),
      activation_history: [activation(3, entry.id, entry.content_hash)],
      entries: [entry],
    });
    vi.mocked(getConfigRegistryEntry).mockResolvedValue(entryDetail(entry));
    vi.mocked(getRunParameterProposals).mockResolvedValue({
      runId: "run-calibration",
      items: [
        {
          id: "fit-result",
          sourceRunId: "run-calibration",
          analysisRecordId: "analysis-fit-r1",
          baseConfigId: "baseline",
          baseContentHash: "sha256:baseline",
          reason: "Peak moved",
          evidenceOutputIds: ["selected-fit"],
          confidence: 0.98,
          deltas: [],
          approval: {
            actor: "nightly-calibration",
            note: "High-confidence fit",
            approvedAt: "2026-07-24T08:00:00Z",
          },
        },
        {
          id: "endpoint-only-result",
          sourceRunId: "run-calibration",
          analysisRecordId: "analysis-endpoint-only-r1",
          baseConfigId: "baseline",
          baseContentHash: "sha256:baseline",
          reason: "Not part of this candidate",
          evidenceOutputIds: [],
          confidence: 0.9,
          deltas: [],
        },
      ],
    });
    vi.mocked(getRunAnalysis).mockResolvedValue({
      id: "analysis-fit-r1",
      title: "Frequency fit",
      key: "fit",
      revision: 1,
      publicationHash: "sha256:analysis-fit-r1",
      publishedAt: "2026-08-17T12:00:00Z",
      subject: "run",
      inputs: [],
      executions: [],
      outputs: [],
    });
    const openRun = vi.fn();

    renderWorkspace(openRun);

    expect(await screen.findByText("Analysis candidate")).toBeInTheDocument();
    expect(screen.getByText("run-calibration")).toBeInTheDocument();
    const evidence = await screen.findAllByRole("article", {
      name: /^Proposal /,
    });
    expect(evidence).toHaveLength(1);
    expect(evidence.map((item) => item.getAttribute("aria-label"))).toEqual([
      "Proposal fit-result",
    ]);
    expect(
      screen.queryByRole("article", {
        name: "Proposal endpoint-only-result",
      }),
    ).not.toBeInTheDocument();
    expect(await screen.findByText("analysis-fit-r1")).toBeInTheDocument();
    expect(screen.getByText("Frequency fit")).toBeInTheDocument();
    expect(screen.getByText("Approved · nightly-calibration")).toBeInTheDocument();
    expect(screen.getByText("analysis-candidate-verification-r1")).toBeInTheDocument();
    expect(screen.getByText("decision")).toBeInTheDocument();
    expect(screen.getByText("High-confidence fit")).toBeInTheDocument();
    expect(document.querySelector('time[datetime="2026-07-24T08:00:00Z"]')).toBeInTheDocument();
    expect(getRunAnalysis).toHaveBeenCalledWith(
      "run-calibration",
      "analysis-fit-r1",
      expect.any(AbortSignal),
    );

    fireEvent.click(screen.getByRole("button", { name: "Open producing run" }));
    expect(openRun).toHaveBeenCalledWith("run-calibration");
  });

  it("keeps approval note and time unresolved while proposals load", async () => {
    const entry = runtimeDerivedEntry("candidate_config");
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(3, entry.id, entry.content_hash),
      activation_history: [activation(3, entry.id, entry.content_hash)],
      entries: [entry],
    });
    vi.mocked(getConfigRegistryEntry).mockResolvedValue(entryDetail(entry));
    vi.mocked(getRunParameterProposals).mockImplementation(
      () => new Promise<Awaited<ReturnType<typeof getRunParameterProposals>>>(() => undefined),
    );

    renderWorkspace();

    const evidence = await screen.findByRole("article", {
      name: "Proposal fit-result",
    });
    expect(within(evidence).getByText("Loading proposal")).toBeInTheDocument();
    expect(within(evidence).getByText("Loading approval")).toBeInTheDocument();
    expect(within(evidence).queryByText("Note")).not.toBeInTheDocument();
    expect(within(evidence).queryByText("Approved")).not.toBeInTheDocument();
  });

  it("does not report missing approval note or time when proposals fail", async () => {
    const entry = runtimeDerivedEntry("candidate_config");
    vi.mocked(getConfigRegistry).mockResolvedValue({
      activation: activation(3, entry.id, entry.content_hash),
      activation_history: [activation(3, entry.id, entry.content_hash)],
      entries: [entry],
    });
    vi.mocked(getConfigRegistryEntry).mockResolvedValue(entryDetail(entry));
    vi.mocked(getRunParameterProposals).mockRejectedValue(new Error("proposal store offline"));

    renderWorkspace();

    expect(
      await screen.findByText("Proposal evidence unavailable: proposal store offline"),
    ).toBeInTheDocument();
    const evidence = screen.getByRole("article", {
      name: "Proposal fit-result",
    });
    expect(within(evidence).getByText("Proposal details unavailable")).toBeInTheDocument();
    expect(within(evidence).getByText("Approval unavailable")).toBeInTheDocument();
    expect(within(evidence).queryByText("Note")).not.toBeInTheDocument();
    expect(within(evidence).queryByText("Approved")).not.toBeInTheDocument();
  });
});

function renderWorkspace(onOpenRun?: (runId: string) => void) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <ConfigWorkspace daemonUnavailable={false} onOpenRun={onOpenRun} />
      </QueryClientProvider>,
    ),
    queryClient,
  };
}

function configEntry(id: string, contentHash: string): ConfigRegistryEntry {
  return {
    id,
    content_hash: contentHash,
    config_ref: `entries/${id}.json`,
    actor: "Ada",
    recorded_at: "2026-07-24T08:00:00Z",
    source: { kind: "direct_config_profile" },
    note: "",
  };
}

function runtimeDerivedEntry(
  kind: "manual_parameter_updates" | "candidate_config" | "calibration_cohort_merge",
  proposalId = "fit-result",
): ConfigRegistryEntry {
  const entry = configEntry("runtime-default", "sha256:runtime-default");
  if (kind === "calibration_cohort_merge") {
    return {
      ...entry,
      source: {
        kind,
        cohort_id: "drag-nightly-2026-07-24",
        spec_hash: `sha256:${"b".repeat(64)}`,
        composition_policy_ref: {
          id: "reference_lab.drag-composition",
          version: "1",
          fingerprint: `sha256:${"c".repeat(64)}`,
        },
        merge_policy: "common_base_cells_v1",
        base_entry_id: "baseline",
        base_config_content_hash: "sha256:baseline",
        base_registry_generation: 2,
        candidate_id: "drag-nightly-merged",
        contributions: [
          {
            member_id: "q0",
            proof: {
              kind: "verified_parameter_proposal_v1",
              evidence_step: {
                procedure_run_id: "procedure-drag-q0",
                step_key: "verification",
                attempt: 1,
              },
              baseline_run_id: "run-baseline-q0",
              fit_analysis_record_id: "analysis-fit-q0",
              proposal_id: "proposal-q0",
              candidate_run_id: "run-candidate-q0",
              decision: {
                analysis_record_id: "analysis-verify-q0",
                output_id: "decision",
                schema_id: "reference_lab.drag-decision.v1",
                schema_hash: `sha256:${"d".repeat(64)}`,
              },
            },
            result_input_fingerprint: `sha256:${"e".repeat(64)}`,
          },
        ],
      },
    };
  }
  return {
    ...entry,
    source:
      kind === "manual_parameter_updates"
        ? {
            kind,
            base_entry_id: "baseline",
            base_config_content_hash: "sha256:baseline",
            base_registry_generation: 2,
          }
        : {
            kind,
            proposal_id: proposalId,
            run_id: "run-calibration",
            base_config_content_hash: "sha256:baseline",
            acceptance: {
              kind: "cross_run_verification",
              decision: {
                analysis_record_id: "analysis-candidate-verification-r1",
                output_id: "decision",
                schema_id: "tests.candidate-decision.v1",
                schema_hash: `sha256:${"a".repeat(64)}`,
              },
            },
          },
  };
}

function activation(
  generation: number,
  entryId: string,
  entryContentHash: string,
  previousEntryId?: string,
  action: "activation" | "inventory_migration" = "activation",
) {
  return {
    generation,
    action,
    entry_id: entryId,
    entry_content_hash: entryContentHash,
    previous_entry_id: previousEntryId,
    actor: "Ada",
    note: "",
    recorded_at: "2026-07-24T08:00:00Z",
  };
}

function entryDetail(entry: ConfigRegistryEntry) {
  return {
    entry,
    config: emptyConfig(entry.id),
    summary: {
      id: entry.id,
      primaryEntityId: "q0",
      parameterCount: 0,
      instrumentCount: 0,
    },
  };
}

function emptyConfig(id: string): ConfigProfileSnapshot {
  return {
    id,
    system: {
      id: "system",
      primary_entity_id: "q0",
      topology: {
        entities: [],
      },
      instrument_registry: { instruments: [] },
      routing: { roles: [], routes: [] },
      domain_target: null,
      parameter_catalog: {
        id: "parameters",
        definitions: [],
      },
    },
    parameter_snapshot: {
      id: "parameters",
      values: [],
    },
  };
}
