export interface ParameterProposalDelta {
  parameterId: string;
  before: unknown;
  after: unknown;
  cells?: {
    key: Record<string, unknown>;
    field: string;
    before?: unknown;
    after?: unknown;
    change_kind: "physical" | "representation" | "added" | "removed";
  }[];
}

export interface ParameterProposalApproval {
  actor: string;
  note?: string;
  approvedAt?: string;
}

export interface ParameterProposal {
  id: string;
  sourceRunId: string;
  analysisRecordId: string;
  baseConfigId: string;
  baseContentHash: string;
  reason: string;
  evidenceOutputIds: string[];
  confidence?: number;
  proposedAt?: string;
  deltas: ParameterProposalDelta[];
  approval?: ParameterProposalApproval;
}

export interface RunParameterProposalPage {
  runId: string;
  items: ParameterProposal[];
  nextCursor?: number;
}

export interface AcceptProposalCommand {
  runId: string;
  proposalId: string;
  actor: string;
  expectedGeneration: number;
  note?: string;
}
