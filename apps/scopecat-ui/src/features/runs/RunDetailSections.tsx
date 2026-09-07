import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Atom,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Cpu,
  Database,
  Gauge,
  LoaderCircle,
  SquareStack,
  XCircle,
} from "lucide-react";
import type {
  MeasurementTracePreview,
  RunExecutionSegment,
  RunExecutionSegmentPage,
} from "../../api-contract";
import { errorMessage, formatRelative, shorten, titleCase } from "../../lib/presentation";
import { classes, countBadge, detailCard, secondaryButton } from "../../ui/styles";
import type {
  ContentEntry,
  MeasurementPreview,
  MeasurementSlicePreview,
  ProjectEvent,
  ProjectRun,
  RunAnalysisSummary,
} from "../../types";
import { AnalysisPublicationView } from "../analyses/AnalysisPublicationView";
import { MeasurementDataPreview } from "./MeasurementDataPreview";
import {
  canPreviewRunContent,
  getRunAnalysis,
  getRunArtifactDownload,
  getRunContent,
} from "./run-api";
import type {
  MeasurementEntitySelection,
  MeasurementTraceQueryPlan,
} from "./measurement-visualization";

export function ProgressCard({
  run,
  events,
  measurements,
}: {
  run: ProjectRun;
  events: ProjectEvent[];
  measurements?: MeasurementPreview;
}) {
  const expected = run.plan.pointCount;
  const completed = Math.max(completedPoints(run, events), measurements?.recordCount ?? 0);
  const terminal = ["succeeded", "failed", "cancelled"].includes(run.status);
  const adaptive = expected === undefined;
  const accepted = run.pointPlan.acceptedPointCount;
  const target = expected ?? (run.pointPlan.closed ? accepted : run.pointPlan.pointLimit);
  const hasProgress = target > 0;
  const progressValue = hasProgress
    ? terminal && run.status === "succeeded" && expected !== undefined
      ? target
      : Math.min(completed, target)
    : undefined;
  const percentage =
    progressValue !== undefined && target ? Math.round((progressValue / target) * 100) : undefined;

  return (
    <article className={classes(detailCard, "col-span-full max-[680px]:col-auto")}>
      <CardHeading
        icon={<Gauge size={17} />}
        title="Execution progress"
        accessory={
          percentage !== undefined ? (
            <strong className="font-mono text-[0.78rem] text-accent">{percentage}%</strong>
          ) : (
            <span className={countBadge}>{run.stateLabel}</span>
          )
        }
      />
      {hasProgress ? (
        <>
          <progress
            className="h-2 w-full appearance-none overflow-hidden rounded-full border-0 bg-panel-strong [&::-moz-progress-bar]:rounded-full [&::-moz-progress-bar]:bg-accent [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-panel-strong [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-accent"
            max={target}
            value={progressValue}
            aria-label={`${progressValue ?? 0} of ${target} points complete`}
          />
          <div className="mt-[7px] flex justify-between text-[0.65rem] text-text-dim max-[460px]:flex-wrap max-[460px]:gap-2 [&_strong]:text-text-soft">
            <span>
              <strong>{progressValue ?? 0}</strong> / {adaptive ? accepted : target} points
              {adaptive && ` accepted · ${run.pointPlan.pointLimit} max`}
            </span>
            <span>
              {adaptive
                ? run.pointPlan.closed
                  ? run.pointPlan.stopReason
                  : `Optimizer attempts ${run.pointPlan.optimizerAttemptCount} · operator requests ${run.pointPlan.operatorRequestCount} · plan open`
                : `${events.length} durable events`}
            </span>
          </div>
        </>
      ) : (
        <div className="flex min-h-[54px] items-center gap-3 rounded-[9px] border border-dashed border-line bg-[rgb(255_255_255_/_1%)] p-3 text-text-dim">
          <Activity className="flex-none text-blue" size={23} aria-hidden="true" />
          <div>
            <strong className="text-[0.75rem] text-text-soft">
              {run.status === "running" ? "Execution is active" : "No point total reported"}
            </strong>
            <p className="mt-1 mb-0 text-[0.67rem] leading-[1.45]">
              {events.length > 0
                ? `${events.length} durable events received from this run.`
                : "Progress will appear when the daemon publishes plan or execution events."}
            </p>
          </div>
        </div>
      )}
      <div className="mt-[13px] grid grid-cols-3 gap-2 max-[460px]:grid-cols-1">
        <Fact
          label="Last update"
          value={run.updatedAt ? formatRelative(run.updatedAt) : "Not reported"}
        />
        <Fact label="Result" value={titleCase(run.result ?? "Pending")} />
        <Fact label="Certainty" value={titleCase(run.certainty ?? "Pending")} />
      </div>
    </article>
  );
}

export function AnalysisCard({
  analyses,
  error,
  pending,
  runId,
  hasNextPage,
  loadingNextPage,
  onLoadOlder,
}: {
  analyses?: RunAnalysisSummary[];
  error: Error | null;
  pending: boolean;
  runId: string;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  onLoadOlder: () => void;
}) {
  const linkedAnalysis = new URLSearchParams(window.location.search).get("run-analysis");
  return (
    <article className={detailCard} data-testid="resource-card">
      <CardHeading
        icon={<Atom size={17} />}
        title="Analyses"
        accessory={
          <span className={countBadge}>
            {analyses?.length ?? 0}
            {hasNextPage ? "+" : ""}
          </span>
        }
      />
      {linkedAnalysis && <LinkedRunAnalysis runId={runId} analysisId={linkedAnalysis} />}
      {error ? (
        <InlineEmpty title="Analyses unavailable" detail={errorMessage(error)} warning />
      ) : pending ? (
        <InlineEmpty
          title="Reading analyses"
          detail="Waiting for the daemon's persisted analysis records."
        />
      ) : !analyses || analyses.length === 0 ? (
        !linkedAnalysis && (
          <InlineEmpty
            title="No analyses saved"
            detail="Notebook and automated analysis outputs will appear here."
          />
        )
      ) : (
        <div className="grid gap-2">
          {analyses
            .filter((analysis) => analysis.id !== linkedAnalysis)
            .map((analysis) => (
              <RunAnalysisItem analysis={analysis} key={analysis.id} runId={runId} />
            ))}
          {hasNextPage && (
            <button
              className={classes(secondaryButton, "w-full")}
              disabled={loadingNextPage}
              onClick={onLoadOlder}
              type="button"
            >
              {loadingNextPage ? (
                <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
              ) : (
                <ChevronDown size={14} aria-hidden="true" />
              )}
              {loadingNextPage ? "Loading older analyses…" : "Load older analyses"}
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export function ExecutionSegmentsCard({
  page,
  error,
  pending,
}: {
  page?: RunExecutionSegmentPage;
  error: Error | null;
  pending: boolean;
}) {
  const segments = [...(page?.items ?? [])].sort((left, right) => left.ordinal - right.ordinal);
  return (
    <article className={detailCard} data-testid="execution-segments-card">
      <CardHeading
        icon={<SquareStack size={17} />}
        title="Execution segments"
        accessory={
          <span className={countBadge}>
            {segments.length}
            {page?.next_cursor ? "+" : ""}
          </span>
        }
      />
      {error ? (
        <InlineEmpty title="Segments unavailable" detail={errorMessage(error)} warning />
      ) : pending ? (
        <InlineEmpty
          title="Reading execution history"
          detail="Waiting for the daemon's durable execution boundaries."
        />
      ) : segments.length === 0 ? (
        <InlineEmpty
          title="No execution segments"
          detail="A segment appears when an executor starts this run."
        />
      ) : (
        <ol className="m-0 grid list-none gap-2 p-0">
          {segments.map((segment) => (
            <ExecutionSegmentItem key={segment.segment_id} segment={segment} />
          ))}
        </ol>
      )}
    </article>
  );
}

function ExecutionSegmentItem({ segment }: { segment: RunExecutionSegment }) {
  const active = segment.ended_at === null || segment.ended_at === undefined;
  const endPoint = active ? "active" : String(segment.end_point_count ?? "ended");
  return (
    <li className="grid gap-2 rounded-[8px] border border-line bg-[rgb(255_255_255_/_1.2%)] p-2.5">
      <div className="flex items-center justify-between gap-2">
        <strong className="text-[0.7rem] text-text-soft">Segment {segment.ordinal + 1}</strong>
        <span
          className={classes(
            "rounded-[5px] border px-[5px] py-[3px] text-[0.56rem] font-extrabold uppercase",
            active
              ? "border-[rgb(128_163_207_/_25%)] bg-accent-soft text-accent"
              : segment.result === "succeeded"
                ? "border-[rgb(128_163_207_/_20%)] bg-blue-soft text-blue"
                : "border-[rgb(237_201_111_/_20%)] bg-yellow-soft text-yellow",
          )}
        >
          {active ? "Active" : titleCase(segment.result ?? "Ended")}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 max-[460px]:grid-cols-1">
        <Fact label="Point coverage" value={`${segment.start_point_count} → ${endPoint}`} />
        <Fact label="Certainty" value={titleCase(segment.certainty ?? "Pending")} />
        <Fact label="Executor" value={shorten(segment.executor_id, 16)} />
        <Fact label="Run contract" value={shorten(segment.run_contract_fingerprint, 16)} />
      </div>
      <p className="m-0 text-[0.61rem] leading-[1.45] text-text-dim">
        Started <time dateTime={segment.started_at}>{formatRelative(segment.started_at)}</time>
        {segment.ended_at ? (
          <>
            {" · ended "}
            <time dateTime={segment.ended_at}>{formatRelative(segment.ended_at)}</time>
          </>
        ) : null}
        {segment.reason ? ` · ${titleCase(segment.reason)}` : ""}
      </p>
    </li>
  );
}

function LinkedRunAnalysis({ runId, analysisId }: { runId: string; analysisId: string }) {
  const detail = useQuery({
    queryKey: ["analysis", runId, analysisId],
    queryFn: ({ signal }) => getRunAnalysis(runId, analysisId, signal),
  });
  if (detail.isPending)
    return (
      <InlineEmpty
        title="Reading linked analysis"
        detail="Loading the exact retained publication."
      />
    );
  if (detail.isError)
    return (
      <InlineEmpty
        title="Linked analysis unavailable"
        detail={errorMessage(detail.error)}
        warning
      />
    );
  return (
    <section className="mb-3 border rounded p-3">
      <h4>{detail.data.title}</h4>
      <AnalysisPublicationView
        analysis={detail.data}
        getArtifactDownload={(selector) => getRunArtifactDownload(runId, selector)}
      />
    </section>
  );
}

function RunAnalysisItem({ analysis, runId }: { analysis: RunAnalysisSummary; runId: string }) {
  const [expanded, setExpanded] = useState(false);
  const detail = useQuery({
    queryKey: ["analysis", runId, analysis.id],
    queryFn: ({ signal }) => getRunAnalysis(runId, analysis.id, signal),
    enabled: expanded,
  });
  return (
    <details
      className="overflow-hidden rounded-[8px] border border-line bg-[rgb(255_255_255_/_1.2%)]"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="flex cursor-pointer items-center justify-between gap-2.5 p-2.5">
        <span className="grid min-w-0 gap-[3px]">
          <strong className="overflow-hidden text-[0.7rem] text-ellipsis whitespace-nowrap">
            {analysis.title}
          </strong>
          <small className="text-[0.6rem] text-text-dim">
            {analysis.key ?? analysis.id}
            {analysis.stepId ? ` · ${analysis.stepId}` : ""}
            {analysis.inputCount > 0 ? ` · ${analysis.inputCount} inputs` : ""}
            {" · "}
            <time dateTime={analysis.publishedAt} title={formatDateTime(analysis.publishedAt)}>
              {formatRelative(analysis.publishedAt)}
            </time>
          </small>
        </span>
        <span className={countBadge}>{analysis.outputCount}</span>
      </summary>
      <div className="px-2.5 pb-2.5">
        {detail.isPending ? (
          <InlineEmpty title="Reading analysis" detail="Loading exact publication details." />
        ) : detail.isError ? (
          <InlineEmpty title="Analysis unavailable" detail={errorMessage(detail.error)} warning />
        ) : (
          <AnalysisPublicationView
            analysis={detail.data}
            getArtifactDownload={(selector) => getRunArtifactDownload(runId, selector)}
          />
        )}
      </div>
    </details>
  );
}

export function ResourceCard({ run }: { run: Pick<ProjectRun, "resources"> }) {
  return (
    <article className={detailCard}>
      <CardHeading
        icon={<Cpu size={17} />}
        title="Resources"
        accessory={<span className={countBadge}>{run.resources.length}</span>}
      />
      {run.resources.length > 0 ? (
        <ul className="m-0 grid list-none gap-[7px] p-0">
          {run.resources.map((resource) => (
            <li
              className="flex min-w-0 items-center gap-[9px] rounded-[8px] border border-line bg-[rgb(255_255_255_/_1.2%)] p-[9px]"
              data-testid={`resource-${resource.id}`}
              key={`${resource.kind}:${resource.id}`}
            >
              <span
                className="grid size-7 flex-none place-items-center rounded-[7px] bg-blue-soft text-blue"
                aria-hidden="true"
              >
                <Box size={15} />
              </span>
              <span className="grid min-w-0 flex-1 gap-0.5">
                <strong className="overflow-hidden text-[0.7rem] font-[650] text-ellipsis whitespace-nowrap">
                  {resource.id}
                </strong>
                <small className="overflow-hidden text-[0.6rem] text-ellipsis whitespace-nowrap text-text-dim">
                  {titleCase(resource.kind)}
                </small>
                {resource.blockedBy && (
                  <small className="break-all text-[0.6rem] text-yellow">
                    Blocked by{" "}
                    {resource.blockedBy.ownerKind === "run" ? "run" : "interactive session"}{" "}
                    {resource.blockedBy.ownerId}
                    {resource.blockedBy.status === "quarantined" && " — reconciliation required"}
                  </small>
                )}
              </span>
              <span
                className={classes(
                  "flex-none rounded-[5px] border px-[5px] py-[3px] text-[0.56rem] font-extrabold uppercase",
                  leaseStatusClass(resource.status),
                )}
              >
                {titleCase(resource.status ?? "claimed")}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <InlineEmpty
          title="No resource claims"
          detail="This run has not announced exclusive hardware resources."
        />
      )}
    </article>
  );
}

export function TimelineCard({
  events,
  error,
  pending,
}: {
  events: ProjectEvent[];
  error: Error | null;
  pending: boolean;
}) {
  return (
    <article
      className={classes(detailCard, "row-span-2 max-[680px]:row-auto")}
      data-testid="timeline-card"
    >
      <CardHeading
        icon={<Activity size={17} />}
        title="Recent events"
        accessory={<span className={countBadge}>{events.length}</span>}
      />
      {error ? (
        <InlineEmpty title="Events unavailable" detail={errorMessage(error)} warning />
      ) : pending ? (
        <InlineEmpty
          title="Reading events"
          detail="Waiting for this run's durable event timeline."
        />
      ) : events.length === 0 ? (
        <InlineEmpty
          title="No events reported"
          detail="The daemon has not returned durable events for this run."
        />
      ) : (
        <>
          {events.length === 500 && (
            <p className="mt-0 mb-3 text-[0.64rem] leading-[1.45] text-text-dim">
              Showing the latest 500 events; older events are not loaded.
            </p>
          )}
          <ol className="m-0 grid max-h-[540px] list-none overflow-auto p-0 pr-[3px] [scrollbar-color:#344252_transparent] [scrollbar-width:thin] max-[680px]:max-h-[430px]">
            {events.map((event) => (
              <li
                className="relative grid grid-cols-[29px_minmax(0,1fr)] gap-2.5 pb-[17px] not-last:before:absolute not-last:before:top-[27px] not-last:before:bottom-[-1px] not-last:before:left-[13px] not-last:before:w-px not-last:before:bg-line not-last:before:content-['']"
                key={event.id}
              >
                <span
                  className="z-[1] grid size-[27px] place-items-center rounded-[8px] border border-line bg-panel text-accent"
                  aria-hidden="true"
                >
                  {eventIcon(event.kind)}
                </span>
                <div>
                  <div className="flex items-baseline justify-between gap-2.5 pt-0.5">
                    <strong className="text-[0.71rem] font-[650]">
                      {humanizeEvent(event.kind)}
                    </strong>
                    <span className="font-mono text-[0.57rem] text-text-dim">#{event.id}</span>
                  </div>
                  <p className="my-1 text-[0.64rem] leading-[1.45] text-text-dim">
                    {eventDescription(event)}
                  </p>
                  <time className="text-[0.58rem] text-[#5f6d7a]" dateTime={event.occurredAt}>
                    {event.occurredAt ? formatDateTime(event.occurredAt) : "Timestamp not reported"}
                  </time>
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </article>
  );
}

export function DataCard({
  run,
  measurements,
  error,
  pending,
  contentsError,
  contentsPending,
  contentsHasNextPage,
  contentsLoadingNextPage,
  onLoadOlderContents,
  measurementSlice,
  measurementSliceError,
  measurementSlicePending,
  tracePlans,
  selectedTracePlanId,
  tracePreview,
  traceError,
  tracePending,
  onTracePlanChange,
  onMeasurementEntitySelectionChange,
  measurementFixedAxisIndices,
  onMeasurementSliceOffsetChange,
  onMeasurementFixedAxisIndexChange,
}: {
  run: ProjectRun;
  measurements?: MeasurementPreview;
  error: Error | null;
  pending: boolean;
  contentsError: Error | null;
  contentsPending: boolean;
  contentsHasNextPage: boolean;
  contentsLoadingNextPage: boolean;
  onLoadOlderContents: () => void;
  measurementSlice?: MeasurementSlicePreview;
  measurementSliceError: Error | null;
  measurementSlicePending: boolean;
  tracePlans: MeasurementTraceQueryPlan[];
  selectedTracePlanId?: string;
  tracePreview?: MeasurementTracePreview;
  traceError: Error | null;
  tracePending: boolean;
  onTracePlanChange: (planId: string) => void;
  onMeasurementEntitySelectionChange: (selection: MeasurementEntitySelection) => void;
  measurementFixedAxisIndices: Record<string, number>;
  onMeasurementSliceOffsetChange: (offset: number) => void;
  onMeasurementFixedAxisIndexChange: (axisId: string, index: number) => void;
}) {
  const [requestedContentKey, setRequestedContentKey] = useState<string>();
  const selectedContent =
    run.contents.find((entry) => contentKey(entry) === requestedContentKey) ??
    run.contents.find(canPreviewRunContent) ??
    run.contents[0];
  const selectedContentKey = selectedContent ? contentKey(selectedContent) : undefined;
  const contentQuery = useQuery({
    queryKey: [
      "run-content",
      run.runId,
      selectedContent?.role,
      selectedContent?.id,
      selectedContent?.kind,
    ],
    queryFn: ({ signal }) => getRunContent(run.runId, selectedContent!, signal),
    enabled: selectedContent !== undefined && canPreviewRunContent(selectedContent),
  });
  const hasPlanMetadata =
    run.plan.pointCount !== undefined ||
    run.plan.coordinateIds.length > 0 ||
    run.plan.recordIds.length > 0;
  return (
    <article
      className={classes(detailCard, "[&>.run-inline-empty]:mt-2.5")}
      data-testid="data-card"
    >
      <CardHeading
        icon={<Database size={17} />}
        title="Data contents"
        accessory={
          <span className={countBadge}>
            {run.contents.length}
            {contentsHasNextPage ? "+" : ""}
          </span>
        }
      />
      {hasPlanMetadata && (
        <div className="mb-[13px] grid grid-cols-3 gap-2 rounded-[8px] border border-line bg-[rgb(255_255_255_/_1%)] p-2.5 max-[460px]:grid-cols-1">
          <Fact
            label={
              run.plan.pointCount === undefined
                ? "Initial / accepted / max points"
                : "Planned points"
            }
            value={
              run.plan.pointCount !== undefined
                ? run.plan.pointCount.toLocaleString()
                : `${run.pointPlan.initialPointCount.toLocaleString()} / ${run.pointPlan.acceptedPointCount.toLocaleString()} / ${run.pointPlan.pointLimit.toLocaleString()}`
            }
          />
          <Fact label="Coordinates" value={String(run.plan.coordinateIds.length)} />
          <Fact label="Records" value={String(run.plan.recordIds.length)} />
        </div>
      )}
      {run.plan.coordinateIds.length > 0 && (
        <TagGroup label="Coordinates" values={run.plan.coordinateIds} />
      )}
      {run.plan.recordIds.length > 0 && (
        <TagGroup label="Record types" values={run.plan.recordIds} />
      )}
      {contentsError && run.contents.length === 0 ? (
        <InlineEmpty
          title="Data contents unavailable"
          detail={errorMessage(contentsError)}
          warning
        />
      ) : contentsPending ? (
        <InlineEmpty
          title="Reading data contents"
          detail="Waiting for the daemon's relational content catalog."
        />
      ) : run.contents.length > 0 ? (
        <div className="grid gap-[7px]">
          <ul className="mt-2.5 grid list-none gap-[7px] p-0">
            {run.contents.map((content) => (
              <li
                key={contentKey(content)}
                className={classes(
                  "flex min-w-0 items-center gap-[9px] rounded-[8px] border border-line bg-[rgb(255_255_255_/_1.2%)]",
                  contentKey(content) === selectedContentKey &&
                    "border-[rgb(128_163_207_/_25%)] bg-accent-soft",
                )}
              >
                <button
                  className="flex w-full cursor-pointer items-center gap-[9px] border-0 bg-transparent p-[9px] text-left text-inherit [&>svg]:flex-none [&>svg]:text-text-dim"
                  type="button"
                  onClick={() => setRequestedContentKey(contentKey(content))}
                  aria-current={contentKey(content) === selectedContentKey ? "true" : undefined}
                >
                  <span
                    className="grid size-7 flex-none place-items-center rounded-[7px] bg-blue-soft text-blue"
                    aria-hidden="true"
                  >
                    {content.role === "dataset" ? (
                      <Database size={15} />
                    ) : (
                      <SquareStack size={15} />
                    )}
                  </span>
                  <span className="grid min-w-0 flex-1 gap-0.5">
                    <strong className="overflow-hidden text-[0.7rem] font-[650] text-ellipsis whitespace-nowrap">
                      {content.label}
                    </strong>
                    <small className="overflow-hidden text-[0.6rem] text-ellipsis whitespace-nowrap text-text-dim">
                      {titleCase(content.role)}
                      {content.detail ? ` · ${content.detail}` : ""}
                    </small>
                  </span>
                  {canPreviewRunContent(content) && <ChevronRight size={15} aria-hidden="true" />}
                </button>
              </li>
            ))}
          </ul>
          {contentsError ? (
            <p className="m-0 text-[0.61rem] text-red" role="status">
              {errorMessage(contentsError)}
            </p>
          ) : null}
          {contentsHasNextPage ? (
            <button
              className={classes(secondaryButton, "w-full")}
              disabled={contentsLoadingNextPage}
              onClick={onLoadOlderContents}
              type="button"
            >
              {contentsLoadingNextPage ? (
                <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
              ) : (
                <ChevronDown size={14} aria-hidden="true" />
              )}
              {contentsLoadingNextPage ? "Loading older contents…" : "Load older contents"}
            </button>
          ) : null}
        </div>
      ) : (
        <InlineEmpty
          title="No materialized contents"
          detail="Dataset, record, and artifact metadata will appear after the daemon publishes it."
        />
      )}
      {selectedContent && (
        <RunContentPanel
          entry={selectedContent}
          content={contentQuery.data?.content}
          format={contentQuery.data?.format}
          error={contentQuery.error}
          pending={contentQuery.isPending}
        />
      )}
      <MeasurementRecords
        key={run.runId}
        preview={measurements}
        error={error}
        pending={pending}
        slice={measurementSlice}
        sliceError={measurementSliceError}
        slicePending={measurementSlicePending}
        tracePlans={tracePlans}
        selectedTracePlanId={selectedTracePlanId}
        tracePreview={tracePreview}
        traceError={traceError}
        tracePending={tracePending}
        onTracePlanChange={onTracePlanChange}
        onMeasurementEntitySelectionChange={onMeasurementEntitySelectionChange}
        fixedAxisIndices={measurementFixedAxisIndices}
        onSliceOffsetChange={onMeasurementSliceOffsetChange}
        onFixedAxisIndexChange={onMeasurementFixedAxisIndexChange}
      />
    </article>
  );
}

function RunContentPanel({
  entry,
  content,
  format,
  error,
  pending,
}: {
  entry: ContentEntry;
  content?: unknown;
  format?: "text" | "json";
  error: Error | null;
  pending: boolean;
}) {
  if (!canPreviewRunContent(entry)) {
    if (entry.role === "dataset") {
      return (
        <InlineEmpty
          title={
            entry.kind === "measurement_dataset"
              ? "Measurement dataset"
              : "Dataset preview unavailable"
          }
          detail={
            entry.kind === "measurement_dataset"
              ? "Use the bounded measurement preview below while records are still arriving."
              : `The daemon does not expose ${entry.kind} through the typed dataset preview.`
          }
        />
      );
    }
    return (
      <InlineEmpty
        title="Binary artifact"
        detail={`${entry.filename ?? entry.id} is recorded, but has no text or JSON preview.`}
      />
    );
  }
  if (error) {
    return <InlineEmpty title="Content unavailable" detail={errorMessage(error)} warning />;
  }
  if (pending || content === undefined) {
    return (
      <InlineEmpty title="Reading content" detail={`Loading ${entry.label} from the daemon.`} />
    );
  }
  return (
    <div className={previewPanel}>
      <div className={previewHeading}>
        <strong className="text-[0.65rem] text-text-soft">{entry.label}</strong>
        <span>{format === "text" ? "Text" : "JSON"}</span>
      </div>
      <pre className={previewContent}>{formatPreviewContent(content)}</pre>
    </div>
  );
}

function MeasurementRecords({
  preview,
  slice,
  sliceError,
  slicePending,
  tracePlans,
  selectedTracePlanId,
  tracePreview,
  traceError,
  tracePending,
  onTracePlanChange,
  onMeasurementEntitySelectionChange,
  fixedAxisIndices,
  onSliceOffsetChange,
  onFixedAxisIndexChange,
  error,
  pending,
}: {
  preview?: MeasurementPreview;
  slice?: MeasurementSlicePreview;
  sliceError: Error | null;
  slicePending: boolean;
  tracePlans: MeasurementTraceQueryPlan[];
  selectedTracePlanId?: string;
  tracePreview?: MeasurementTracePreview;
  traceError: Error | null;
  tracePending: boolean;
  onTracePlanChange: (planId: string) => void;
  onMeasurementEntitySelectionChange: (selection: MeasurementEntitySelection) => void;
  fixedAxisIndices: Record<string, number>;
  onSliceOffsetChange: (offset: number) => void;
  onFixedAxisIndexChange: (axisId: string, index: number) => void;
  error: Error | null;
  pending: boolean;
}) {
  if (error) {
    return (
      <InlineEmpty title="Measurement preview unavailable" detail={errorMessage(error)} warning />
    );
  }
  if (pending) {
    return (
      <InlineEmpty
        title="Reading measurements"
        detail="Waiting for the daemon's bounded record preview."
      />
    );
  }
  if (!preview || (preview.items.length === 0 && preview.schema === undefined)) {
    return (
      <InlineEmpty
        title="No measurement records"
        detail="Measurements appear here as the daemon receives them."
      />
    );
  }
  return (
    <MeasurementDataPreview
      preview={preview}
      slice={slice}
      sliceError={sliceError}
      slicePending={slicePending}
      tracePlans={tracePlans}
      selectedTracePlanId={selectedTracePlanId}
      tracePreview={tracePreview}
      traceError={traceError}
      tracePending={tracePending}
      onTracePlanChange={onTracePlanChange}
      onEntitySelectionChange={onMeasurementEntitySelectionChange}
      fixedAxisIndices={fixedAxisIndices}
      onSliceOffsetChange={onSliceOffsetChange}
      onFixedAxisIndexChange={onFixedAxisIndexChange}
    />
  );
}

function CardHeading({
  icon,
  title,
  accessory,
}: {
  icon: ReactNode;
  title: string;
  accessory?: ReactNode;
}) {
  return (
    <div className="mb-[15px] grid min-h-[26px] grid-cols-[26px_minmax(0,1fr)_auto] items-center gap-[7px]">
      <span
        className="grid size-[26px] place-items-center rounded-[7px] border border-line bg-panel text-accent"
        aria-hidden="true"
      >
        {icon}
      </span>
      <h3 className="m-0 text-[0.78rem] font-bold">{title}</h3>
      {accessory && <div className="text-[0.68rem] text-text-dim">{accessory}</div>}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid min-w-0 gap-1">
      <span className="text-[0.6rem] font-bold tracking-[0.05em] text-text-dim uppercase">
        {label}
      </span>
      <strong className="overflow-hidden text-[0.72rem] font-[650] text-ellipsis whitespace-nowrap text-text-soft">
        {value}
      </strong>
    </div>
  );
}

function TagGroup({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="mb-3">
      <span className="mb-1.5 block text-[0.58rem] font-extrabold tracking-[0.06em] text-text-dim uppercase">
        {label}
      </span>
      <div className="flex flex-wrap gap-[5px]">
        {values.map((value) => (
          <code
            className="rounded-[5px] border border-line bg-[rgb(182_156_255_/_7%)] px-1.5 py-1 text-[0.59rem] text-purple"
            key={value}
          >
            {value}
          </code>
        ))}
      </div>
    </div>
  );
}

function InlineEmpty({
  title,
  detail,
  warning = false,
}: {
  title: string;
  detail: string;
  warning?: boolean;
}) {
  return (
    <div
      className={classes(
        "run-inline-empty flex items-start gap-[9px] rounded-[8px] border border-dashed border-line p-3 text-text-dim [&>svg]:mt-px [&>svg]:flex-none",
        warning && "border-[rgb(255_140_136_/_20%)] bg-red-soft [&>svg]:text-red",
      )}
    >
      {warning ? (
        <XCircle size={17} aria-hidden="true" />
      ) : (
        <CircleDot size={17} aria-hidden="true" />
      )}
      <div>
        <strong className="block text-[0.69rem] text-text-soft">{title}</strong>
        <p className="mt-1 mb-0 text-[0.62rem] leading-[1.45]">{detail}</p>
      </div>
    </div>
  );
}

const previewPanel = "mt-3 overflow-hidden rounded-[8px] border border-line bg-panel";
const previewHeading =
  "flex items-center justify-between border-b border-line px-2.5 py-2 text-[0.6rem] text-text-dim";
const previewContent =
  "m-0 max-h-[260px] overflow-auto p-2.5 text-[0.6rem] leading-normal text-[#aebfd0] [scrollbar-color:#344252_transparent] [scrollbar-width:thin]";

function leaseStatusClass(status?: string): string {
  if (status === "quarantined" || status === "blocked") {
    return "border-[rgb(237_201_111_/_20%)] bg-yellow-soft text-yellow";
  }
  if (status === "required") {
    return "border-[rgb(120_184_255_/_20%)] bg-blue-soft text-blue";
  }
  if (status === "released") {
    return "border-line bg-panel text-text-dim";
  }
  return "border-[rgb(128_163_207_/_18%)] bg-accent-soft text-accent";
}

function completedPoints(run: ProjectRun, events: ProjectEvent[]): number {
  const completedPointIndices = new Set<number>();
  for (const event of events) {
    if (event.kind !== "execution_transition_committed" || event.payload.state !== "completed") {
      continue;
    }
    const evidence = event.payload.evidence;
    if (event.payload.stage === "point" && typeof evidence === "object" && evidence !== null) {
      const pointIndices = (evidence as Record<string, unknown>).point_indices;
      if (Array.isArray(pointIndices)) {
        for (const pointIndex of pointIndices) {
          if (typeof pointIndex === "number") completedPointIndices.add(pointIndex);
        }
      }
      const pointIndex = event.payload.point_index;
      if (typeof pointIndex === "number") completedPointIndices.add(pointIndex);
    }
    if (
      event.payload.stage === "append_measurement" &&
      typeof evidence === "object" &&
      evidence !== null
    ) {
      const record = evidence as Record<string, unknown>;
      const startIndex = record.start_index;
      const recordCount = record.record_count;
      if (typeof startIndex === "number" && typeof recordCount === "number") {
        for (let offset = 0; offset < recordCount; offset += 1) {
          completedPointIndices.add(startIndex + offset);
        }
      }
    }
  }
  // Only durable transition facts survive daemon reconnects.
  return Math.max(run.progressCompleted ?? 0, completedPointIndices.size);
}

function eventIcon(kind: string): ReactNode {
  if (kind.includes("admit") || kind.includes("accept")) {
    return <CheckCircle2 size={14} />;
  }
  if (kind.includes("resource") || kind.includes("lease")) {
    return <Cpu size={14} />;
  }
  if (kind.includes("state") || kind.includes("transition")) {
    return <Activity size={14} />;
  }
  return <CircleDot size={14} />;
}

function eventDescription(event: ProjectEvent): string {
  const primitiveEntries = Object.entries(event.payload)
    .filter(
      ([key, value]) =>
        !["kind", "run_id"].includes(key) &&
        (typeof value === "string" || typeof value === "number" || typeof value === "boolean"),
    )
    .slice(0, 3);
  if (primitiveEntries.length === 0) {
    return "Durable project event committed by the daemon.";
  }
  return primitiveEntries.map(([key, value]) => `${titleCase(key)}: ${String(value)}`).join(" · ");
}

function humanizeEvent(kind: string): string {
  const specific: Record<string, string> = {
    run_admitted: "Run admitted",
    run_state_changed: "Run state changed",
    resources_claimed: "Resources acquired",
    executor_lease_granted: "Executor connected",
    executor_lease_lost: "Executor lost",
    execution_transition_committed: "Execution transition",
  };
  return specific[kind] ?? titleCase(kind);
}

function contentKey(entry: ContentEntry): string {
  return `${entry.role}:${entry.id}`;
}

function formatPreviewContent(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2) ?? String(value);
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}
