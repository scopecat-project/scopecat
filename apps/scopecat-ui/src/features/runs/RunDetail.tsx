import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { CopyReadOnlyCode } from "../../ui/CopyReadOnlyCode";
import { OpenRunWindow } from "./OpenRunWindow";
import { WindowTitle } from "../application/WindowTitle";
import { ExportRun } from "./ExportRun";
import { RunPlanOrigin } from "../launch/PlanOrigin";
import { ComparisonLink } from "../analyses/ComparisonLink";
import { AlertTriangle, Boxes, ChevronRight, Unlock } from "lucide-react";
import { RunCancellationNotice } from "./RunCancellationNotice";
import type {
  MeasurementTracePreview,
  RunDomainDecisionPage,
  RunExecutionSegmentPage,
} from "../../api-contract";
import { errorMessage, formatDateTime, shorten } from "../../lib/presentation";
import { classes } from "../../ui/styles";
import type {
  MeasurementPreview,
  MeasurementSlicePreview,
  ProjectEvent,
  ProjectRun,
  RunAnalysisSummary,
} from "../../types";
import { RunProposals } from "../proposals/RunProposals";
import { RunMeasuredCosts } from "./RunMeasuredCosts";
import { RunAnalysisFollows } from "./RunAnalysisFollows";
import { RunFailureEvidence } from "./RunFailureEvidence";
import { RunDomainDecisionCard } from "./RunDomainDecisionCard";
import type {
  MeasurementEntitySelection,
  MeasurementTraceQueryPlan,
} from "./measurement-visualization";
import {
  AnalysisCard,
  DataCard,
  ExecutionSegmentsCard,
  ProgressCard,
  ResourceCard,
  TimelineCard,
} from "./RunDetailSections";

import { ClearData } from "../history/DataCleanup";

export function RunDetail({
  run,
  events,
  eventsError,
  eventsPending,
  executionSegments,
  executionSegmentsError,
  executionSegmentsPending,
  domainDecisions,
  domainDecisionsError,
  domainDecisionsPending,
  measurements,
  measurementsError,
  measurementsPending,
  liveMeasurementsError,
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
  analyses,
  analysesError,
  analysesPending,
  analysesHasNextPage,
  analysesLoadingNextPage,
  onLoadOlderAnalyses,
  contentsError,
  contentsPending,
  contentsHasNextPage,
  contentsLoadingNextPage,
  onLoadOlderContents,
  attentionError,
  attentionPending,
  onResolveAttention,
  onOpenSample,
}: {
  run: ProjectRun;
  events: ProjectEvent[];
  eventsError: Error | null;
  eventsPending: boolean;
  executionSegments?: RunExecutionSegmentPage;
  executionSegmentsError: Error | null;
  executionSegmentsPending: boolean;
  domainDecisions?: RunDomainDecisionPage;
  domainDecisionsError: Error | null;
  domainDecisionsPending: boolean;
  measurements?: MeasurementPreview;
  measurementsError: Error | null;
  measurementsPending: boolean;
  liveMeasurementsError?: Error | null;
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
  analyses?: RunAnalysisSummary[];
  analysesError: Error | null;
  analysesPending: boolean;
  analysesHasNextPage: boolean;
  analysesLoadingNextPage: boolean;
  onLoadOlderAnalyses: () => void;
  contentsError: Error | null;
  contentsPending: boolean;
  contentsHasNextPage: boolean;
  contentsLoadingNextPage: boolean;
  onLoadOlderContents: () => void;
  attentionError: Error | null;
  attentionPending: boolean;
  onResolveAttention: () => void;
  onOpenSample?: (sampleId: string, revision: number) => void;
}) {
  return (
    <>
      <WindowTitle title={`${run.displayName ?? run.experimentId} · ${run.runId} — Scopecat`} />
      <header
        className="flex items-start justify-between gap-7 border-b border-line px-0.5 pb-3 max-[680px]:block"
        data-testid="run-detail-header"
      >
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <h2 className="m-0 text-[clamp(1.2rem,1.8vw,1.55rem)] font-[650] tracking-[-0.035em] [overflow-wrap:anywhere]">
              {run.displayName ?? run.experimentId}
            </h2>
            <span
              className={classes(
                "inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[0.62rem] font-extrabold tracking-[0.04em] uppercase",
                runStatusBadge[run.status],
              )}
              data-testid="run-status"
            >
              <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
              {run.stateLabel}
            </span>
            <CopyReadOnlyCode target={{ kind: "run", runId: run.runId }} compact />
          </div>
          <div className="flex max-w-[min(60vw,620px)] items-center gap-2 overflow-hidden text-[0.68rem] text-text-dim max-[680px]:max-w-full">
            {run.displayName && (
              <code
                className="overflow-hidden text-ellipsis whitespace-nowrap"
                title={run.experimentId}
              >
                {run.experimentId}
              </code>
            )}
            {run.displayName && <span aria-hidden="true">·</span>}
            <code className="overflow-hidden text-ellipsis whitespace-nowrap" title={run.runId}>
              {run.runId}
            </code>
          </div>
        </div>
        <dl className="mt-1 flex flex-none gap-7 max-[1100px]:gap-[18px] max-[680px]:mt-5 max-[680px]:grid max-[680px]:grid-cols-2 max-[460px]:grid-cols-1">
          <div className="grid gap-1.5">
            <dt className="text-[0.6rem] font-extrabold tracking-[0.09em] text-text-dim uppercase">
              Accepted
            </dt>
            <dd className="m-0 text-[0.69rem] text-text-soft">
              {run.createdAt ? (
                <time dateTime={run.createdAt}>{formatDateTime(run.createdAt)}</time>
              ) : (
                "Not reported"
              )}
            </dd>
          </div>
          <div className="grid gap-1.5">
            <dt className="text-[0.6rem] font-extrabold tracking-[0.09em] text-text-dim uppercase">
              Config
            </dt>
            <dd className="m-0 text-[0.69rem] text-text-soft">
              <code title={run.configHash}>
                {run.configHash ? shorten(run.configHash, 15) : "Not reported"}
              </code>
            </dd>
          </div>
        </dl>
      </header>
      <div className="my-3 flex flex-wrap items-center gap-3 text-sm">
        <OpenRunWindow key={`open:${run.runId}`} runId={run.runId} />
        <ComparisonLink runId={run.runId} />
        <ExportRun key={`export:${run.runId}`} runId={run.runId} />
        <details className="text-sm text-text-soft">
          <summary className="cursor-pointer rounded-md border border-line px-3 py-2">
            {run.scenario ? "Software scenario" : "Execution context · not declared"}
          </summary>
          <ExecutionScenario scenario={run.scenario} label="Retained execution scenario" />
        </details>
      </div>
      <RunPlanOrigin runId={run.runId} />
      <RunFailureEvidence runId={run.runId} />

      {run.samples.length > 0 && (
        <section
          className="mt-[18px] flex flex-wrap items-center gap-2 rounded-md border border-line bg-panel-soft px-3 py-2.5"
          aria-label="Bound samples"
        >
          <span className="mr-1 inline-flex items-center gap-2 text-[0.62rem] font-extrabold tracking-[0.08em] text-text-dim uppercase">
            <Boxes size={14} aria-hidden="true" />
            Samples
          </span>
          {run.samples.map((sample) => (
            <button
              key={`${sample.role}:${sample.sample_id}:${sample.context_id ?? ""}`}
              className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-[rgb(128_163_207_/_20%)] bg-accent-soft px-2.5 py-1.5 text-left text-[0.66rem] text-text-soft hover:border-line-strong hover:bg-panel-strong disabled:cursor-default"
              type="button"
              title={`${sample.sample_id} · exact revision ${sample.revision}`}
              disabled={!onOpenSample}
              onClick={() => onOpenSample?.(sample.sample_id, sample.revision)}
            >
              <span className="grid gap-0.5">
                <strong className="font-[650] text-accent">{sample.display_name}</strong>
                <span className="text-[0.58rem] text-text-dim">
                  {sample.role} · r{sample.revision}
                  {sample.context_id ? ` · ${sample.context_id}` : ""}
                </span>
              </span>
              {onOpenSample && <ChevronRight size={13} aria-hidden="true" />}
            </button>
          ))}
        </section>
      )}

      {run.resources.some(
        (resource) => resource.blockedBy || resource.status === "quarantined",
      ) && <ResourceCard run={run} />}
      <RunCancellationNotice run={run} />
      {run.status === "attention_required" && (
        <div
          className="mt-[18px] flex items-start gap-[11px] rounded-md border border-[rgb(237_201_111_/_23%)] bg-yellow-soft px-3.5 py-[13px]"
          role="alert"
        >
          <AlertTriangle className="flex-none text-yellow" size={19} aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <strong className="text-[0.77rem] text-[#fae4ad]">Operator attention required</strong>
            <p className="mt-1 mb-0 text-[0.71rem] leading-normal text-[#c4b994]">
              {run.attentionReason ?? "The daemon has not reported a reconciliation reason."}
            </p>
            <p className="mt-1 mb-0 text-[0.71rem] leading-normal text-[#c4b994]">
              Inspect retained data and reconcile external state using the project workflow. Unknown
              hardware effects cannot be retried here. Closing this record does not establish that
              the hardware is safe.
            </p>
            <div className="mt-[11px] flex flex-wrap gap-[7px]">
              <button
                className="inline-flex min-h-[31px] cursor-pointer items-center gap-1.5 rounded-[7px] border border-[rgb(255_140_136_/_35%)] bg-red-soft px-2.5 text-red hover:not-disabled:bg-panel-strong hover:not-disabled:text-text disabled:cursor-wait disabled:opacity-55"
                type="button"
                onClick={onResolveAttention}
                disabled={attentionPending}
              >
                <Unlock size={15} aria-hidden="true" />
                {attentionPending ? "Closing…" : "Close without resuming"}
              </button>
            </div>
            {attentionError && (
              <p className="mt-1 mb-0 text-[0.71rem] leading-normal text-red" role="status">
                {errorMessage(attentionError)}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="mt-4 grid min-w-0 gap-4">
        <ProgressCard
          run={run}
          events={events}
          measurements={measurements}
          receiptError={liveMeasurementsError}
        />
        <DataCard
          run={run}
          measurements={measurements}
          error={measurementsError}
          pending={measurementsPending}
          contentsError={contentsError}
          contentsPending={contentsPending}
          contentsHasNextPage={contentsHasNextPage}
          contentsLoadingNextPage={contentsLoadingNextPage}
          onLoadOlderContents={onLoadOlderContents}
          measurementSlice={measurementSlice}
          measurementSliceError={measurementSliceError}
          measurementSlicePending={measurementSlicePending}
          tracePlans={tracePlans}
          selectedTracePlanId={selectedTracePlanId}
          tracePreview={tracePreview}
          traceError={traceError}
          tracePending={tracePending}
          onTracePlanChange={onTracePlanChange}
          onMeasurementEntitySelectionChange={onMeasurementEntitySelectionChange}
          measurementFixedAxisIndices={measurementFixedAxisIndices}
          onMeasurementSliceOffsetChange={onMeasurementSliceOffsetChange}
          onMeasurementFixedAxisIndexChange={onMeasurementFixedAxisIndexChange}
        />
        <AnalysisCard
          analyses={analyses}
          error={analysesError}
          pending={analysesPending}
          runId={run.runId}
          hasNextPage={analysesHasNextPage}
          loadingNextPage={analysesLoadingNextPage}
          onLoadOlder={onLoadOlderAnalyses}
        />
        <RunAnalysisFollows runId={run.runId} />
        <details
          className="rounded-md border border-line p-4"
          open={run.plan.pointCount === undefined || undefined}
        >
          <summary className="cursor-pointer text-sm font-semibold">
            Proposals and adaptive controls
          </summary>
          <div className="mt-3 grid gap-3">
            <RunDomainDecisionCard
              page={domainDecisions}
              error={domainDecisionsError}
              pending={domainDecisionsPending}
              completedPointCount={run.progressCompleted ?? 0}
              run={run}
            />
            <RunProposals key={run.runId} runId={run.runId} />
          </div>
        </details>
        <details className="rounded-md border border-line p-4">
          <summary className="cursor-pointer text-sm font-semibold">
            Execution diagnostics and provenance
          </summary>
          <div className="mt-3 grid gap-3 lg:grid-cols-2">
            <ExecutionSegmentsCard
              page={executionSegments}
              error={executionSegmentsError}
              pending={executionSegmentsPending}
            />
            <ResourceCard run={run} />
            <TimelineCard events={events} error={eventsError} pending={eventsPending} />

            <RunMeasuredCosts runId={run.runId} />
            <ClearData key={`cleanup:${run.runId}`} runs={[run.runId]} />
          </div>
        </details>
      </div>
    </>
  );
}
const runStatusBadge: Record<ProjectRun["status"], string> = {
  accepted: "border-[rgb(120_184_255_/_20%)] bg-blue-soft text-blue",
  running: "border-[rgb(128_163_207_/_20%)] bg-accent-soft text-accent",
  attention_required: "border-[rgb(237_201_111_/_20%)] bg-yellow-soft text-yellow",
  succeeded: "border-[rgb(128_163_207_/_20%)] bg-accent-soft text-accent",
  failed: "border-[rgb(255_140_136_/_20%)] bg-red-soft text-red",
  cancelled: "border-[rgb(255_140_136_/_20%)] bg-red-soft text-red",
};
