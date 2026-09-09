import type { components } from "./api-schema";
import type { ClientPathsWithMethod, MethodResponse } from "openapi-fetch";
import type { apiClient } from "./api-client";

type GetResponse<Path extends ClientPathsWithMethod<typeof apiClient, "get">> = MethodResponse<
  typeof apiClient,
  "get",
  Path
>;
type PostResponse<Path extends ClientPathsWithMethod<typeof apiClient, "post">> = MethodResponse<
  typeof apiClient,
  "post",
  Path
>;

export type RunControlView = components["schemas"]["RunControlView"];
export type ConfigActivationRecord = components["schemas"]["ConfigRegistryActivationRecord"];
export type ConfigDraftCommand = components["schemas"]["ConfigDraftCommand"];
export type ConfigPublishCommand = components["schemas"]["ConfigPublishCommand"];
export type ConfigPublishReceipt = Omit<
  PostResponse<"/api/v1/config-registry/publish-operations">,
  "deltas"
> & {
  deltas: ParameterValueDelta[];
};
export type ConfigDraftPreview = Omit<
  PostResponse<"/api/v1/config-registry/drafts/preview">,
  "config" | "deltas"
> & {
  config?: ConfigProfileSnapshot | null;
  deltas: ParameterValueDelta[];
};
export type ConfigProfileSnapshot = components["schemas"]["ConfigProfileSnapshot-Input"];
export type ConfigRegistryEntry = components["schemas"]["ConfigRegistryEntry"];
export type ConfigRegistryPage = GetResponse<"/api/v1/config-registry">;
export type ConfigActivationPage = GetResponse<"/api/v1/config-registry/activations">;
export type ConfigRegistryOverview = Omit<ConfigRegistryPage, "next_cursor"> & {
  activation_history: ConfigActivationRecord[];
  entries_next_cursor?: number;
  activation_history_next_cursor?: number;
};
export type DriverCatalog = GetResponse<"/api/v1/instrument-drivers">;
export type DriverConnectionSpec = components["schemas"]["DriverConnectionSpec"];
export type DriverSpec = components["schemas"]["DriverSpec"];
export type DurableEvent = components["schemas"]["DurableEvent"];
export type EntityRef = components["schemas"]["EntityRef"];
export type ExternalLocation = components["schemas"]["ExternalLocation"];
export type InstrumentAcquisition = components["schemas"]["AcquisitionSpec"];
export type InstrumentApplyReceipt =
  PostResponse<"/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state/apply">;
export type InstrumentConfiguredDefaultsApplyReceipt =
  PostResponse<"/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/configured-defaults/apply">;
export type InstrumentComponent = components["schemas"]["ComponentSpec"];
export type InstrumentCollectReceipt = components["schemas"]["CollectReceipt"];
export type InstrumentConnection = components["schemas"]["InstrumentConnection"];
export type InstrumentDescription = components["schemas"]["InstrumentDescription"];
export type InstrumentDriverProbeCommand = components["schemas"]["InstrumentDriverProbeCommand"];
export type InstrumentDriverProbeReceipt = PostResponse<"/api/v1/instrument-drivers/probe">;
export type InstrumentInterface = components["schemas"]["InterfaceSpec"];
export type InstrumentInvokeCommand = components["schemas"]["InvokeCommand"];
export type InstrumentInvokeReceipt =
  PostResponse<"/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/invoke">;
export type InstrumentOperation = components["schemas"]["OperationSpec"];
export type InstrumentProperty = components["schemas"]["PropertySpec"];
export type InstrumentDeviceState = components["schemas"]["DeviceStateSpec"];
export type InstrumentInterfaceMount = components["schemas"]["InterfaceMountSpec"];
export type InstrumentInterfacePropertyImplementation =
  components["schemas"]["InterfacePropertyImplementationSpec"];
export type InstrumentSession = PostResponse<"/api/v1/instrument-sessions">;
export type InstrumentSessionLease =
  PostResponse<"/api/v1/instrument-sessions/{session_id}/heartbeat">;
export type InstrumentSpec = components["schemas"]["InstrumentSpec"];
export type InstrumentState =
  GetResponse<"/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state">;
export type InstrumentStateCache =
  PostResponse<"/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state/observed">;
export type InstrumentStateReadback =
  PostResponse<"/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state/read">;
export type InstrumentStateObservation = components["schemas"]["InstrumentStateObservation"];
export type InstrumentStateSetting = components["schemas"]["InstrumentStateSetting"];
export type InstrumentStateTarget = components["schemas"]["StateMemberTarget"];
export type InstrumentStateValue = components["schemas"]["StateValue"];
export type InstrumentView = GetResponse<"/api/v1/instruments/{instrument_id}">;
export type AnalysisFigureView = components["schemas"]["AnalysisFigureView"];
export type AnalysisArtifactReference = components["schemas"]["AnalysisArtifactReference"];
export type AnalysisDatasetReference = components["schemas"]["AnalysisDatasetReference"];
export type AnalysisDatasetDerivation = components["schemas"]["AnalysisDatasetDerivation"];
export type AnalysisExecution = components["schemas"]["AnalysisExecution"];
export type AnalysisExecutionOutputReference =
  components["schemas"]["AnalysisExecutionOutputReference"];
export type AnalysisFact = components["schemas"]["AnalysisFact"];
export type AnalysisRecordInput = components["schemas"]["AnalysisRecordInput"];
export type AnalysisRecordOutput = components["schemas"]["AnalysisRecordOutput"];
export type AnalysisParameterProposalReference =
  components["schemas"]["AnalysisParameterProposalReference"];
export type AnalysisTableView = components["schemas"]["AnalysisTableView"];
export type AnalysisContentBytes =
  GetResponse<"/api/v1/analyses/{analysis_id}/contents/{selector}/bytes">;
export type ProjectAnalysisPage = GetResponse<"/api/v1/analyses">;
export type ProjectAnalysisSummary = components["schemas"]["ProjectAnalysisSummary"];
export type ProjectAnalysisView = GetResponse<"/api/v1/analyses/{selector}">;
export type MeasurementDatasetSchema = components["schemas"]["MeasurementDatasetSchema-Output"];
export type MeasurementAcquisitionValue = components["schemas"]["MeasurementAcquisitionValue"];
export type MeasurementRecord = components["schemas"]["MeasurementRecord"];
export type MeasurementSlice = components["schemas"]["MeasurementSlice"];
export type MeasurementTracePreview = components["schemas"]["MeasurementTracePreview"];
export type MeasurementTracePreviewQuery = components["schemas"]["MeasurementTracePreviewQuery"];
export type MeasurementValue = components["schemas"]["MeasurementValue"];
/** Complex scalar representation on the JSON wire. */
export type ComplexComponents = Extract<
  Extract<MeasurementValue, { kind: "scalar" }>["value"],
  { imag: number; real: number }
>;
export type ActiveConfig = GetResponse<"/api/v1/config-registry/active">;
export type InstrumentList = GetResponse<"/api/v1/instruments">;
export type ParameterProposalPage = GetResponse<"/api/v1/runs/{run_id}/parameter-proposals">;
export type RunSummaryPage = GetResponse<"/api/v1/runs">;
export type EventPage = GetResponse<"/api/v1/events">;
export type ParameterAtom = components["schemas"]["ParameterAtomValue"];
export type ParameterDefinition = components["schemas"]["ParameterDefinition"];
export type ParameterEntity = components["schemas"]["EntityRef"];
export type ParameterUpdate = components["schemas"]["ConfigDraftCommand"]["updates"][number];
export type ParameterQuantity = components["schemas"]["scopecat__kernel__quantity__Quantity"];
export type ParameterScalarType = components["schemas"]["PersistableScalarWire"];
export type ParameterValueDelta = Omit<
  components["schemas"]["ParameterValueDelta-Output"],
  "before" | "after"
> & {
  before: StoredParameterValue;
  after: StoredParameterValue;
};
export type ParameterValueType = components["schemas"]["PersistableValueType"];
export type ProcedureRunPage = GetResponse<"/api/v1/procedures">;
export type ProcedureRun = ProcedureRunPage["items"][number];
export type ProcedureStepAttemptPage = GetResponse<"/api/v1/procedures/{procedure_run_id}/steps">;
export type ProcedureStepAttempt = ProcedureStepAttemptPage["items"][number];
export type ProcedureStepInputSubmitCommand =
  components["schemas"]["ProcedureStepInputSubmitCommand"];
export type ProcedureStepInputSubmitReceipt =
  PostResponse<"/api/v1/procedures/{procedure_run_id}/steps/{step_key}/attempts/{attempt}/input">;
export type Quantity = components["schemas"]["scopecat__kernel__quantity__Quantity"];
export type ReviewCompileCommand = components["schemas"]["ReviewCompileCommand"];
export type ProgramInspectionQuery = components["schemas"]["CompiledProgramInspectionQuery"];
export type PointCoordinateSpec = components["schemas"]["PointCoordinateSpec"];
export type ReviewSession = GetResponse<"/api/v1/reviews/{session_id}">;
export type ReviewSessionList = GetResponse<"/api/v1/reviews">;
export type ReviewInspection = components["schemas"]["ReviewInspectionView-Output"];
export type RunDomainDecisionPage = GetResponse<"/api/v1/runs/{run_id}/point-plan/decisions">;
export type AdaptiveRegion = components["schemas"]["AdaptiveRegionSpec"];
export type RunDomainAxis = components["schemas"]["RunDomainAxisView"];
export type RunDomainEnqueueCommand = components["schemas"]["RunDomainEnqueueCommand"];
export type RunDomainResolveCommand = components["schemas"]["RunDomainResolveCommand"];
export type ResolvedRunDomain = components["schemas"]["ResolvedRunDomainView"];
export type RunDomainQueue = GetResponse<"/api/v1/runs/{run_id}/point-plan/queue">;
export type CompiledArtifactInspection = components["schemas"]["CompiledArtifactInspection-Output"];
export type CompiledPointInspection = components["schemas"]["CompiledPointInspection-Output"];
export type CompiledWaveformInspection = components["schemas"]["CompiledWaveformInspection"];
export type ContentEntryView = components["schemas"]["ContentEntry"];
export type RunAnalysisPage = components["schemas"]["RunAnalysisPage"];
export type RunAnalysisSummary = components["schemas"]["RunAnalysisSummary"];
export type RunAnalysisView = components["schemas"]["RunAnalysisView"];
export type RunContentPage = GetResponse<"/api/v1/runs/{run_id}/contents">;
export type RunExecutionSegment = components["schemas"]["RunExecutionSegment"];
export type RunExecutionSegmentPage = GetResponse<"/api/v1/runs/{run_id}/execution-segments">;
export type RunSnapshot = components["schemas"]["RunSnapshot"];
export type RunResourceView = components["schemas"]["RunResourceView"];
export type SampleAnalysisPage = GetResponse<"/api/v1/samples/{sample_id}/analyses">;
export type SampleAnalysisView = GetResponse<"/api/v1/samples/{sample_id}/analyses/{selector}">;
export type SampleBinding = components["schemas"]["SampleBinding"];
export type SampleGeometry = components["schemas"]["SampleGeometry"];
export type SamplePage = GetResponse<"/api/v1/samples">;
export type SampleRevision = GetResponse<"/api/v1/samples/{sample_id}/revisions/{revision}">;
export type SampleRevisionPage = GetResponse<"/api/v1/samples/{sample_id}/revisions">;
export type SampleSummary = SamplePage["items"][number];
export type SampleView = GetResponse<"/api/v1/samples/{sample_id}">;
export type StoredParameterValue = components["schemas"]["StoredParameterValue-Input"];
export type TableParameterType = Extract<ParameterValueType, { shape: "table" }>;
export type TableParameterValue = Extract<StoredParameterValue, { shape: "table" }>;
