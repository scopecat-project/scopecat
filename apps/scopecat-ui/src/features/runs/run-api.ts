import type {
  DurableEvent,
  EventPage,
  MeasurementTracePreview,
  MeasurementTracePreviewQuery,
  RunAnalysisPage,
  RunAnalysisSummary as RunAnalysisSummaryView,
  RunAnalysisView,
  ContentEntryView,
  RunContentPage as RunContentPageView,
  RunControlView,
  RunSnapshot,
  RunResourceView,
  RunSummaryPage,
  RunDomainDecisionPage,
  ResolvedRunDomain,
  RunDomainEnqueueCommand,
  RunDomainQueue,
  RunDomainResolveCommand,
  RunExecutionSegmentPage,
} from "../../api-contract";
import { ApiError, apiClient, apiData } from "../../api-client";
import { decodeMeasurementArrowRecord } from "./measurement-arrow";
import type {
  ContentEntry,
  MeasurementPreview,
  MeasurementLivePreview,
  MeasurementSlicePreview,
  ProjectEvent,
  ProjectRun,
  ProjectRunContentPage,
  ProjectRunPage,
  PresentationRunStatus,
  RunAnalysis,
  RunAnalysisSummary,
  RunAnalysisSummaryPage,
  RunContentPreview,
  RunResource,
} from "../../types";
import { analysisOutput } from "../analyses/analysis-model";

type RunResourceRequirement =
  RunControlView["admission"]["plan"]["run_resource_requirements"][number];
export async function closeAttentionRun(runId: string): Promise<void> {
  await apiData(
    apiClient.POST("/api/v1/runs/{run_id}/attention", {
      params: { path: { run_id: runId } },
      body: { disposition: "close" },
    }),
  );
}

export async function getRuns(signal?: AbortSignal, sampleId?: string): Promise<ProjectRunPage> {
  return normalizeRunPage(
    await apiData(
      apiClient.GET("/api/v1/runs", {
        params: { query: { limit: 100, sample_id: sampleId } },
        signal,
      }),
    ),
  );
}

export async function getOlderRuns(
  before: number,
  signal?: AbortSignal,
  sampleId?: string,
): Promise<ProjectRunPage> {
  return normalizeRunPage(
    await apiData(
      apiClient.GET("/api/v1/runs", {
        params: { query: { limit: 100, before, sample_id: sampleId } },
        signal,
      }),
    ),
  );
}

function normalizeRunPage(response: RunSummaryPage): ProjectRunPage {
  return {
    items: response.items.map((run) => normalizeRun(run.control, run.snapshot)).sort(compareRuns),
    nextCursor: response.next_cursor ?? undefined,
  };
}

export async function getRun(runId: string, signal?: AbortSignal): Promise<ProjectRun> {
  const response = await apiData(
    apiClient.GET("/api/v1/runs/{run_id}", {
      params: { path: { run_id: runId } },
      signal,
    }),
  );
  return normalizeRun(response.control, response.snapshot, response.resources ?? []);
}

export async function getRunContents(
  runId: string,
  signal?: AbortSignal,
): Promise<ProjectRunContentPage> {
  return getRunContentPage(runId, undefined, signal);
}

export async function getOlderRunContents(
  runId: string,
  before: number,
  signal?: AbortSignal,
): Promise<ProjectRunContentPage> {
  return getRunContentPage(runId, before, signal);
}

async function getRunContentPage(
  runId: string,
  before: number | undefined,
  signal?: AbortSignal,
): Promise<ProjectRunContentPage> {
  const response = await apiData(
    apiClient.GET("/api/v1/runs/{run_id}/contents", {
      params: {
        path: { run_id: runId },
        query: { limit: 100, before },
      },
      signal,
    }),
  );
  return normalizeRunContentPage(response);
}

export async function getRunDomainDecisions(
  runId: string,
  signal?: AbortSignal,
): Promise<RunDomainDecisionPage> {
  return apiData(
    apiClient.GET("/api/v1/runs/{run_id}/point-plan/decisions", {
      params: { path: { run_id: runId }, query: { limit: 64 } },
      signal,
    }),
  );
}

export async function getRunExecutionSegments(
  runId: string,
  signal?: AbortSignal,
): Promise<RunExecutionSegmentPage> {
  return apiData(
    apiClient.GET("/api/v1/runs/{run_id}/execution-segments", {
      params: { path: { run_id: runId }, query: { limit: 100 } },
      signal,
    }),
  );
}

export async function getRunDomainQueue(
  runId: string,
  signal?: AbortSignal,
): Promise<RunDomainQueue> {
  return apiData(
    apiClient.GET("/api/v1/runs/{run_id}/point-plan/queue", {
      params: { path: { run_id: runId } },
      signal,
    }),
  );
}

export async function enqueueRunDomain(
  runId: string,
  command: RunDomainEnqueueCommand,
): Promise<RunDomainQueue["items"][number]> {
  return apiData(
    apiClient.POST("/api/v1/runs/{run_id}/point-plan/queue", {
      params: { path: { run_id: runId } },
      body: command,
    }),
  );
}

export async function resolveRunDomain(
  runId: string,
  command: RunDomainResolveCommand,
  signal?: AbortSignal,
): Promise<ResolvedRunDomain> {
  return apiData(
    apiClient.POST("/api/v1/runs/{run_id}/point-plan/resolve", {
      params: { path: { run_id: runId } },
      body: command,
      signal,
    }),
  );
}

export async function getMeasurementPreview(
  runId: string,
  signal?: AbortSignal,
): Promise<MeasurementPreview> {
  const response = await apiData(
    apiClient.GET("/api/v1/runs/{run_id}/measurements/preview", {
      params: {
        path: { run_id: runId },
        query: { limit: 100 },
      },
      signal,
    }),
  );
  return {
    items: response.items ?? [],
    schema: response.dataset_schema ?? undefined,
    truncated: response.truncated ?? false,
  };
}

export async function getMeasurementLivePreview(
  runId: string,
  signal?: AbortSignal,
  afterRecordCount?: number,
): Promise<MeasurementLivePreview> {
  const url = new URL(
    `/api/v1/runs/${encodeURIComponent(runId)}/measurements/live`,
    globalThis.location?.origin ?? "http://localhost",
  );
  if (afterRecordCount !== undefined) {
    url.searchParams.set("after_record_count", String(afterRecordCount));
  }
  let response: Response;
  try {
    response = await globalThis.fetch(url, {
      headers: { Accept: "application/vnd.apache.arrow.file" },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("The local daemon did not respond.");
  }
  if (!response.ok) {
    throw new ApiError(
      `The daemon returned ${response.status} ${response.statusText}.`,
      response.status,
    );
  }
  const receivedRecordCount = requiredCountHeader(response, "X-Scopecat-Received-Record-Count");
  const durableRecordCount = requiredCountHeader(response, "X-Scopecat-Durable-Record-Count");
  const active = response.headers.get("X-Scopecat-Measurement-Active") === "true";
  const content = await response.arrayBuffer();
  return {
    active,
    latest: content.byteLength === 0 ? undefined : decodeMeasurementArrowRecord(content),
    receivedRecordCount,
    durableRecordCount,
  };
}

function requiredCountHeader(response: Response, name: string): number {
  const encoded = response.headers.get(name);
  const value = encoded === null ? Number.NaN : Number(encoded);
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new ApiError(`The daemon returned an invalid ${name} header.`);
  }
  return value;
}

export async function getMeasurementSlice(
  runId: string,
  fixedAxisIndices: Record<string, number>,
  variableIds: string[],
  offset = 0,
  signal?: AbortSignal,
): Promise<MeasurementSlicePreview> {
  const response = await apiData(
    apiClient.POST("/api/v1/runs/{run_id}/measurements/query", {
      params: { path: { run_id: runId } },
      body: {
        fixed_axis_indices: fixedAxisIndices,
        include_schema: false,
        limit: 4096,
        offset,
        variable_ids: variableIds,
      },
      signal,
    }),
  );
  return {
    items: response.items,
    schema: response.dataset_schema ?? undefined,
    selectedPointCount: response.selected_point_count,
    offset: response.offset ?? 0,
    windowPointCount: response.window_point_count ?? response.items.length,
    nextOffset: response.next_offset ?? undefined,
    previousOffset: response.previous_offset ?? undefined,
    truncated: response.truncated,
  };
}

export async function getMeasurementTracePreview(
  runId: string,
  selection: {
    observableId: string;
    coordinateId?: string;
    fixedAxisIndices: Record<string, number>;
    valueMode: NonNullable<MeasurementTracePreviewQuery["value_mode"]>;
    entityIndices?: readonly number[];
  },
  signal?: AbortSignal,
): Promise<MeasurementTracePreview> {
  return apiData(
    apiClient.POST("/api/v1/runs/{run_id}/measurements/traces/query", {
      params: { path: { run_id: runId } },
      body: {
        coordinate_id: selection.coordinateId,
        downsampling: "minmax",
        fixed_axis_indices: selection.fixedAxisIndices,
        entity_indices: selection.entityIndices ? [...selection.entityIndices] : undefined,
        max_samples: 4096,
        max_series: 32,
        observable_id: selection.observableId,
        value_mode: selection.valueMode,
      },
      signal,
    }),
  );
}

export async function getRunAnalysisSummaries(
  runId: string,
  signal?: AbortSignal,
): Promise<RunAnalysisSummaryPage> {
  return getRunAnalysisSummaryPage(runId, undefined, signal);
}

export async function getOlderRunAnalysisSummaries(
  runId: string,
  before: number,
  signal?: AbortSignal,
): Promise<RunAnalysisSummaryPage> {
  return getRunAnalysisSummaryPage(runId, before, signal);
}

async function getRunAnalysisSummaryPage(
  runId: string,
  before: number | undefined,
  signal: AbortSignal | undefined,
): Promise<RunAnalysisSummaryPage> {
  return normalizeRunAnalysisPage(
    await apiData(
      apiClient.GET("/api/v1/runs/{run_id}/analyses", {
        params: {
          path: { run_id: runId },
          query: { limit: 100, before },
        },
        signal,
      }),
    ),
  );
}

export async function getRunAnalysis(
  runId: string,
  selector: string,
  signal?: AbortSignal,
): Promise<RunAnalysis> {
  return normalizeRunAnalysis(
    await apiData(
      apiClient.GET("/api/v1/runs/{run_id}/analyses/{selector}", {
        params: { path: { run_id: runId, selector } },
        signal,
      }),
    ),
  );
}

function normalizeRunAnalysis({ entry, analysis, published_at }: RunAnalysisView): RunAnalysis {
  return {
    id: entry.id,
    title: analysis.title,
    key: analysis.key ?? undefined,
    stepId: analysis.step_id ?? undefined,
    revision: analysis.revision,
    publicationHash: analysis.publication_hash,
    publishedAt: published_at,
    subject: "run" as const,
    inputs: analysis.inputs ?? [],
    executions: analysis.executions ?? [],
    outputs: analysis.outputs.map(analysisOutput),
  };
}

function normalizeRunAnalysisPage(response: RunAnalysisPage): RunAnalysisSummaryPage {
  return {
    items: response.items.map(normalizeRunAnalysisSummary),
    nextCursor: response.next_cursor ?? undefined,
  };
}

function normalizeRunAnalysisSummary(summary: RunAnalysisSummaryView): RunAnalysisSummary {
  return {
    id: summary.entry.id,
    title: summary.title,
    key: summary.key ?? undefined,
    stepId: summary.step_id ?? undefined,
    revision: summary.revision,
    publicationHash: summary.publication_hash,
    publishedAt: summary.published_at,
    inputCount: summary.input_count,
    outputCount: summary.output_count,
  };
}

export async function getRunArtifactDownload(
  runId: string,
  selector: string,
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  const response = await apiData(
    apiClient.GET("/api/v1/runs/{run_id}/artifacts/{selector}/bytes", {
      params: {
        path: { run_id: runId, selector },
        query: { expected_kind: "analysis_artifact" },
      },
      signal,
    }),
  );
  const binary = atob(response.content_base64);
  const buffer = new ArrayBuffer(binary.length);
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return {
    blob: new Blob([buffer], {
      type: response.artifact.media_type ?? "application/octet-stream",
    }),
    filename: response.artifact.filename ?? selector,
  };
}

export function canPreviewRunContent(entry: ContentEntry): boolean {
  return (
    entry.role === "record" || (entry.role === "artifact" && artifactFormat(entry) !== undefined)
  );
}

export async function getRunContent(
  runId: string,
  entry: ContentEntry,
  signal?: AbortSignal,
): Promise<RunContentPreview> {
  if (entry.role === "record") {
    const response = await apiData(
      apiClient.GET("/api/v1/runs/{run_id}/records/{selector}/json", {
        params: {
          path: { run_id: runId, selector: entry.id },
          query: { expected_kind: entry.kind },
        },
        signal,
      }),
    );
    return {
      entry: normalizeContentEntry(response.record, 0),
      format: "json",
      content: response.content,
    };
  }
  if (entry.role === "dataset") {
    const response = await apiData(
      apiClient.GET("/api/v1/runs/{run_id}/datasets/{selector}", {
        params: { path: { run_id: runId, selector: entry.id } },
        signal,
      }),
    );
    return {
      entry: normalizeContentEntry(response.dataset_entry, 0),
      format: "json",
      content: response.dataset,
    };
  }

  const format = artifactFormat(entry);
  if (!format) {
    throw new ApiError("This artifact does not have a browser-readable format.");
  }
  if (format === "text") {
    const response = await apiData(
      apiClient.GET("/api/v1/runs/{run_id}/artifacts/{selector}/text", {
        params: {
          path: { run_id: runId, selector: entry.id },
          query: { expected_kind: entry.kind },
        },
        signal,
      }),
    );
    return {
      entry: normalizeContentEntry(response.artifact, 0),
      format,
      content: response.content,
    };
  }
  const response = await apiData(
    apiClient.GET("/api/v1/runs/{run_id}/artifacts/{selector}/json", {
      params: {
        path: { run_id: runId, selector: entry.id },
        query: { expected_kind: entry.kind },
      },
      signal,
    }),
  );
  return {
    entry: normalizeContentEntry(response.artifact, 0),
    format,
    content: response.content,
  };
}

export async function getRunEvents(runId: string, signal?: AbortSignal): Promise<ProjectEvent[]> {
  return normalizeEvents(
    await apiData(
      apiClient.GET("/api/v1/events", {
        params: { query: { limit: 500, latest: true, run_id: runId } },
        signal,
      }),
    ),
  );
}

function normalizeEvents(response: EventPage): ProjectEvent[] {
  return response.items.map(normalizeEvent).sort((left, right) => left.id - right.id);
}

function normalizeRun(
  control: RunControlView,
  snapshot: RunSnapshot,
  detailResources?: RunResourceView[],
): ProjectRun {
  const admission = control.admission;
  const outcome = snapshot.outcome ?? undefined;
  const plan = admission.plan;
  const status = normalizeStatus(control, snapshot);
  return {
    sequence: control.sequence,
    runId: admission.run_id,
    experimentId: plan.experiment_id,
    displayName: admission.display_name ?? undefined,
    tags: admission.tags ?? [],
    description: admission.description ?? undefined,
    status,
    stateLabel: statusLabel(status),
    createdAt: admission.admitted_at,
    updatedAt: control.updated_at,
    configHash: snapshot.config_content_hash,
    attentionReason: control.attention_reason ?? undefined,
    result: outcome?.result,
    certainty: outcome?.certainty,
    progressCompleted: control.completed_point_count,
    pointPlan: {
      initialPointCount: control.point_plan.initial_point_count,
      acceptedPointCount: control.point_plan.accepted_point_count,
      pointLimit: control.point_plan.point_limit,
      decisionCount: control.point_plan.decision_count,
      optimizerAttemptCount: control.point_plan.optimizer_attempt_count,
      operatorRequestCount: control.point_plan.operator_request_count,
      closed: control.point_plan.plan_closed,
      stopReason: control.point_plan.stop_reason ?? undefined,
    },
    plan: {
      pointCount: plan.point_count ?? undefined,
      initialPointCount: plan.initial_point_count,
      pointLimit: plan.point_limit,
      coordinateIds: plan.coordinates.map((coordinate) => coordinate.id),
      coordinateSpecs: plan.coordinates,
      adaptiveCoordinateIds: plan.adaptive_coordinate_ids,
      adaptiveScope: plan.adaptive_scope ?? undefined,
      perRegionPointLimit: plan.per_region_point_limit ?? undefined,
      adaptiveRegionCount: plan.adaptive_region_count,
      adaptiveRegions: plan.adaptive_regions,
      adaptiveRegionsTruncated: plan.adaptive_regions_truncated,
      sampledPoints: plan.sampled_points,
      sampledPointsTruncated: plan.sampled_points_truncated,
      recordIds: plan.record_ids ?? [],
    },
    resources:
      detailResources !== undefined
        ? detailResources.map(normalizeRunResource)
        : (plan.run_resource_requirements ?? []).map(normalizeResourceRequirement),
    samples: snapshot.samples ?? [],
    contents: [],
  };
}

function normalizeRunContentPage(response: RunContentPageView): ProjectRunContentPage {
  return {
    items: response.items.map(normalizeContentEntry),
    nextCursor: response.next_cursor ?? undefined,
  };
}

function normalizeEvent(event: DurableEvent): ProjectEvent {
  return {
    id: event.event_id,
    runId: event.run_id ?? undefined,
    kind: event.kind,
    occurredAt: event.occurred_at,
    payload: event.payload ?? {},
  };
}

function normalizeResourceRequirement(resource: RunResourceRequirement): RunResource {
  return {
    id: resource.id,
    kind: resource.kind ?? "instrument",
  };
}

function normalizeRunResource(resource: RunResourceView): RunResource {
  return {
    id: resource.resource.id,
    kind: resource.resource.kind ?? "instrument",
    status: resource.status,
    blockedBy: resource.blocked_by
      ? {
          ownerKind: resource.blocked_by.owner_kind,
          ownerId: resource.blocked_by.owner_id,
          status: resource.blocked_by.status,
        }
      : undefined,
  };
}

function normalizeContentEntry(entry: ContentEntryView, index: number): ContentEntry {
  const mediaType = entry.media_type ?? undefined;
  const filename = entry.filename ?? undefined;
  return {
    id: entry.id,
    role: entry.role,
    kind: entry.kind,
    label: entry.title ?? filename ?? `${titleCase(entry.role)} ${index + 1}`,
    detail: mediaType ?? entry.kind,
    mediaType,
    filename,
  };
}

function artifactFormat(entry: ContentEntry): RunContentPreview["format"] | undefined {
  const mediaType = entry.mediaType?.toLocaleLowerCase();
  const filename = entry.filename?.toLocaleLowerCase();
  if (
    mediaType === "application/json" ||
    mediaType?.endsWith("+json") ||
    filename?.endsWith(".json")
  ) {
    return "json";
  }
  if (
    mediaType?.startsWith("text/") ||
    filename?.endsWith(".txt") ||
    filename?.endsWith(".md") ||
    filename?.endsWith(".csv")
  ) {
    return "text";
  }
  return undefined;
}

function normalizeStatus(control: RunControlView, snapshot: RunSnapshot): PresentationRunStatus {
  if (control.state === "attention_required") {
    return "attention_required";
  }
  switch (snapshot.outcome?.result) {
    case "succeeded":
      return "succeeded";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
  }
  return control.state === "leased" ? "running" : "accepted";
}

function statusLabel(status: PresentationRunStatus): string {
  switch (status) {
    case "accepted":
      return "Accepted";
    case "running":
      return "Running";
    case "attention_required":
      return "Needs attention";
    case "succeeded":
      return "Succeeded";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
  }
}

function compareRuns(left: ProjectRun, right: ProjectRun): number {
  if (left.sequence !== undefined && right.sequence !== undefined) {
    return right.sequence - left.sequence;
  }
  return (right.updatedAt ?? "").localeCompare(left.updatedAt ?? "");
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}
