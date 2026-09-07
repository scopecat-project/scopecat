import type {
  AdaptiveRegion,
  AnalysisArtifactReference,
  AnalysisDatasetDerivation,
  AnalysisDatasetReference,
  AnalysisExecution,
  AnalysisExecutionOutputReference,
  AnalysisFact,
  AnalysisFigureView,
  AnalysisParameterProposalReference,
  AnalysisRecordInput,
  AnalysisTableView,
  MeasurementDatasetSchema,
  MeasurementRecord,
  PointCoordinateSpec,
  SampleBinding,
} from "./api-contract";

export type PresentationRunStatus =
  | "accepted"
  | "running"
  | "attention_required"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface RunResource {
  id: string;
  kind: string;
  status?: string;
  blockedBy?: {
    ownerKind: "run" | "instrument_session";
    ownerId: string;
    status: "active" | "quarantined";
  };
}

export interface ContentEntry {
  id: string;
  role: string;
  kind: string;
  label: string;
  detail?: string;
  mediaType?: string;
  filename?: string;
}

export interface RunPlanSummary {
  pointCount?: number;
  initialPointCount: number;
  pointLimit: number;
  coordinateIds: string[];
  coordinateSpecs: PointCoordinateSpec[];
  adaptiveCoordinateIds: string[];
  adaptiveScope?: "per_region" | "global";
  perRegionPointLimit?: number;
  adaptiveRegionCount: number;
  adaptiveRegions: AdaptiveRegion[];
  adaptiveRegionsTruncated: boolean;
  sampledPoints: Record<string, unknown>[];
  sampledPointsTruncated: boolean;
  recordIds: string[];
}

export interface RunPointPlanProgress {
  initialPointCount: number;
  acceptedPointCount: number;
  pointLimit: number;
  decisionCount: number;
  optimizerAttemptCount: number;
  operatorRequestCount: number;
  closed: boolean;
  stopReason?: string;
}

export interface ProjectRun {
  sequence?: number;
  runId: string;
  experimentId: string;
  displayName?: string;
  tags: string[];
  description?: string;
  status: PresentationRunStatus;
  stateLabel: string;
  createdAt?: string;
  updatedAt?: string;
  configHash?: string;
  attentionReason?: string;
  result?: string;
  certainty?: string;
  progressCompleted?: number;
  pointPlan: RunPointPlanProgress;
  plan: RunPlanSummary;
  resources: RunResource[];
  samples: SampleBinding[];
  contents: ContentEntry[];
}

export interface ProjectRunPage {
  items: ProjectRun[];
  nextCursor?: number;
}

export interface ProjectRunContentPage {
  items: ContentEntry[];
  nextCursor?: number;
}

export interface ProjectAnalysisSummaryPage {
  items: ProjectAnalysisSummary[];
  nextCursor?: number;
}

export interface RunAnalysisSummaryPage {
  items: RunAnalysisSummary[];
  nextCursor?: number;
}

export interface ProjectEvent {
  id: number;
  runId?: string;
  kind: string;
  occurredAt?: string;
  payload: Record<string, unknown>;
}

export interface ProjectHealth {
  status: string;
  projectId: string;
  projectName: string;
  projectRoot: string;
  details: Record<string, unknown>;
}

export interface MeasurementPreview {
  items: MeasurementRecord[];
  schema?: MeasurementDatasetSchema;
  truncated?: boolean;
  recordCount?: number;
  durableRecordCount?: number;
  livePointIndex?: number;
}

export interface MeasurementLivePreview {
  active: boolean;
  latest?: MeasurementRecord;
  receivedRecordCount: number;
  durableRecordCount: number;
}

export interface MeasurementSlicePreview {
  items: MeasurementRecord[];
  schema?: MeasurementDatasetSchema;
  selectedPointCount: number;
  offset: number;
  windowPointCount: number;
  nextOffset?: number;
  previousOffset?: number;
  truncated: boolean;
}

interface AnalysisOutputBase {
  id: string;
  title: string;
  producedBy?: AnalysisExecutionOutputReference;
  derivedFrom?: AnalysisDatasetDerivation;
  metadata: Record<string, unknown>;
}

export type AnalysisOutput = AnalysisOutputBase &
  (
    | { kind: "fact"; content: AnalysisFact }
    | { kind: "dataset"; content: AnalysisDatasetReference }
    | { kind: "artifact"; content: AnalysisArtifactReference }
    | { kind: "table"; content: AnalysisTableView }
    | { kind: "figure"; content: AnalysisFigureView }
    | { kind: "parameter_change_proposal"; content: AnalysisParameterProposalReference }
  );

export interface AnalysisPublication {
  id: string;
  title: string;
  key?: string;
  stepId?: string;
  revision: number;
  publicationHash: string;
  publishedAt: string;
  subject: "run" | "project" | "sample";
  inputs: AnalysisRecordInput[];
  executions: AnalysisExecution[];
  outputs: AnalysisOutput[];
}

export interface RunAnalysis extends AnalysisPublication {
  subject: "run";
}

export interface RunAnalysisSummary {
  id: string;
  title: string;
  key?: string;
  stepId?: string;
  revision: number;
  publicationHash: string;
  publishedAt: string;
  inputCount: number;
  outputCount: number;
}

export interface ProjectAnalysis extends AnalysisPublication {
  subject: "project";
}

export interface ProjectAnalysisSummary {
  id: string;
  title: string;
  key: string;
  stepId?: string;
  revision: number;
  publicationHash: string;
  publishedAt: string;
  inputCount: number;
  outputCount: number;
}

export interface RunContentPreview {
  entry: ContentEntry;
  format: "text" | "json";
  content: unknown;
}
