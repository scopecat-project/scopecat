import { apiClient, apiData } from "../../api-client";
import type { ParameterProposalPage } from "../../api-contract";
import type {
  ParameterProposal,
  ParameterProposalApproval,
  ParameterProposalDelta,
  RunParameterProposalPage,
} from "./types";

type WireProposalView = NonNullable<ParameterProposalPage["items"]>[number];
type WireProposalDelta = WireProposalView["proposal"]["deltas"][number];
type WireProposalApproval = NonNullable<WireProposalView["approval"]>;

export async function getRunParameterProposals(
  runId: string,
  signal?: AbortSignal,
): Promise<RunParameterProposalPage> {
  return getRunParameterProposalPage(runId, undefined, signal);
}

export async function getOlderRunParameterProposals(
  runId: string,
  before: number,
  signal?: AbortSignal,
): Promise<RunParameterProposalPage> {
  return getRunParameterProposalPage(runId, before, signal);
}

async function getRunParameterProposalPage(
  runId: string,
  before: number | undefined,
  signal?: AbortSignal,
): Promise<RunParameterProposalPage> {
  const response = await apiData(
    apiClient.GET("/api/v1/runs/{run_id}/parameter-proposals", {
      params: { path: { run_id: runId }, query: { limit: 100, before } },
      signal,
    }),
  );
  return {
    runId: response.run_id,
    items: (response.items ?? []).map(normalizeProposalView),
    nextCursor: response.next_cursor ?? undefined,
  };
}

function normalizeProposalView(source: WireProposalView): ParameterProposal {
  return {
    id: source.proposal.id,
    sourceRunId: source.proposal.source_run_id,
    analysisRecordId: source.proposal.analysis_record_id,
    baseConfigId: source.proposal.base_config_id,
    baseContentHash: source.proposal.base_config_content_hash,
    reason: source.proposal.reason,
    evidenceOutputIds: source.proposal.evidence_output_ids ?? [],
    confidence: source.proposal.confidence ?? undefined,
    proposedAt: source.proposal.proposed_at,
    deltas: source.proposal.deltas.map(normalizeDelta),
    approval: source.approval ? normalizeApproval(source.approval) : undefined,
  };
}

function normalizeDelta(source: WireProposalDelta): ParameterProposalDelta {
  return {
    parameterId: source.parameter_id,
    cells: source.cells ?? undefined,
    before: parameterValue(source.before),
    after: parameterValue(source.after),
  };
}

function normalizeApproval(source: WireProposalApproval): ParameterProposalApproval {
  return {
    actor: source.actor,
    note: source.note || undefined,
    approvedAt: source.approved_at,
  };
}

function parameterValue(value: WireProposalDelta["before"]): unknown {
  switch (value.shape) {
    case "scalar":
      return value.value;
    case "table":
      return value.rows;
    case undefined:
      throw new Error("The daemon returned a proposal value without its shape.");
  }
}
