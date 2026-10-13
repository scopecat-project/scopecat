import { RelatedAnalyses } from "../analyses/RelatedAnalyses";
import { useRunCollectionName } from "./use-run-collection-name";
import { RunAddressLabel } from "./RunAddressLabel";
import { ReturnToPreparation } from "../launch/ReturnToPreparation";
import { RunAnalysisActionsCard } from "./DeclaredRunAnalyses";
import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { CopyReadOnlyCode, CopyRunReference } from "../../ui/CopyReadOnlyCode";
import { OpenRunWindow } from "./OpenRunWindow";
import { WindowTitle } from "../application/WindowTitle";
import { ExportRun } from "./ExportRun";
import { RunPlanOrigin } from "../launch/PlanOrigin";
import { ComparisonLink } from "../analyses/ComparisonLink";
import { Boxes, ChevronRight } from "lucide-react";
import { RunCancellationNotice } from "./RunCancellationNotice";
import type {
  MeasurementTracePreview,
  RunDomainDecisionPage,
  RunExecutionSegmentPage,
} from "../../api-contract";
import { formatDateTime, shorten } from "../../lib/presentation";
import { classes } from "../../ui/styles";
import type {
  MeasurementPreview,
  MeasurementSlicePreview,
  ProjectEvent,
  ProjectRun,
} from "../../types";
import { RunProposals } from "../proposals/RunProposals";
import { RunMeasuredCosts } from "./RunMeasuredCosts";
import { RunAnalysisFollows } from "./RunAnalysisFollows";
import { RunAttentionNotice } from "./RunAttentionNotice";
import { RunFailureEvidence } from "./RunFailureEvidence";
import { RunDomainDecisionCard } from "./RunDomainDecisionCard";
import type {
  MeasurementSliceSelection,
  MeasurementEntitySelection,
  MeasurementTraceQueryPlan,
} from "./measurement-visualization";
import {
  DataCard,
  ExecutionSegmentsCard,
  ProgressCard,
  ResourceCard,
  TimelineCard,
} from "./RunDetailSections";

import { ClearData } from "../history/DataCleanup";

export function RunDetail({
  run,
  projectId,
  collectionNames,
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
  measurementAxes,
  onMeasurementAxesChange,
  onMeasurementSliceOffsetChange,
  onMeasurementFixedAxisIndexChange,
  contentsError,
  contentsPending,
  contentsHasNextPage,
  contentsLoadingNextPage,
  onLoadOlderContents,
  attentionError,
  attentionPending,
  onResolveAttention,
  onOpenSample,
  onOpenRun,
}: {
  run: ProjectRun;
  projectId?: string;
  collectionNames: Map<string, string>;
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
  measurementAxes?: MeasurementSliceSelection;
  onMeasurementAxesChange?: (selection: MeasurementSliceSelection) => void;
  onMeasurementSliceOffsetChange: (offset: number) => void;
  onMeasurementFixedAxisIndexChange: (axisId: string, index: number) => void;
  contentsError: Error | null;
  contentsPending: boolean;
  contentsHasNextPage: boolean;
  contentsLoadingNextPage: boolean;
  onLoadOlderContents: () => void;
  attentionError: Error | null;
  attentionPending: boolean;
  onResolveAttention: () => void;
  onOpenRun?: (runId: string) => void;
  onOpenSample?: (sampleId: string, revision: number) => void;
}) {
  const collection = useRunCollectionName(projectId, run.address?.collection_id, collectionNames);
  const collectionName = collection.name;
  return (
    <>
      <WindowTitle
        title={`${run.address ? `${collectionName ?? "Collection name unavailable"} #${run.address.number} · ` : ""}${run.displayName ?? run.experimentId} — Scopecat`}
      />
      <header
        className="flex items-start justify-between gap-3 border-b border-line px-0.5 pb-2 max-[680px]:flex-wrap"
        data-testid="run-detail-header"
      >
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-2">
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
            {run.address && (
              <CopyRunReference
                runId={run.runId}
                address={run.address}
                collectionName={collectionName}
              />
            )}
            <CopyReadOnlyCode
              target={{ kind: "run", runId: run.runId, address: run.address }}
              compact
            />
          </div>
          <RunAddressLabel
            address={run.address}
            sequence={run.sequence}
            collectionName={collectionName}
          />
          {collection.error && (
            <p role="alert" className="text-xs">
              {collection.error.message}
            </p>
          )}
        </div>
        <details className="text-xs text-text-soft">
          <summary className="cursor-pointer py-1">Run details</summary>
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            <div>
              <dt>Experiment</dt>
              <dd className="m-0 break-all">
                <code>{run.experimentId}</code>
              </dd>
            </div>
            <div>
              <dt>Run ID</dt>
              <dd className="m-0 break-all">
                <code title={run.runId}>{run.runId}</code>
              </dd>
            </div>
            <div>
              <dt>Collection ID</dt>
              <dd className="m-0 break-all">
                <code>{run.address?.collection_id ?? "Not reported"}</code>
              </dd>
            </div>
            <div>
              <dt>Global number</dt>
              <dd className="m-0">{run.sequence ?? "Not reported"}</dd>
            </div>

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
        </details>
      </header>
      <div className="my-2 flex flex-wrap items-center gap-2 text-sm">
        <ReturnToPreparation />
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

      <div className="mt-2 grid min-w-0 gap-3">
        <DataCard
          notices={
            <>
              <RunAttentionNotice
                run={run}
                onResolve={onResolveAttention}
                pending={attentionPending}
                error={attentionError}
              />
              <RunFailureEvidence runId={run.runId} />
              {run.resources.some(
                (resource) => resource.blockedBy || resource.status === "quarantined",
              ) && <ResourceCard run={run} />}
            </>
          }
          progress={
            <>
              <RunCancellationNotice run={run} />
              <ProgressCard
                run={run}
                events={events}
                measurements={measurements}
                receiptError={liveMeasurementsError}
              />
            </>
          }
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
          measurementAxes={measurementAxes}
          onMeasurementAxesChange={onMeasurementAxesChange}
          onMeasurementSliceOffsetChange={onMeasurementSliceOffsetChange}
          onMeasurementFixedAxisIndexChange={onMeasurementFixedAxisIndexChange}
        />
        {run.status === "succeeded" && <RunAnalysisActionsCard key={run.runId} runId={run.runId} />}
        {projectId && (
          <RelatedAnalyses
            key={`${projectId}:${run.runId}`}
            projectId={projectId}
            runId={run.runId}
            collectionNames={collectionNames}
            onOpenRun={onOpenRun}
          />
        )}
        <RunProposals key={run.runId} runId={run.runId} />
        <RunAnalysisFollows runId={run.runId} />
        <details
          className="rounded-md border border-line p-4"
          open={run.plan.pointCount === undefined || undefined}
        >
          <summary className="cursor-pointer text-sm font-semibold">Adaptive controls</summary>
          <div className="mt-3 grid gap-3">
            <RunDomainDecisionCard
              page={domainDecisions}
              error={domainDecisionsError}
              pending={domainDecisionsPending}
              completedPointCount={run.progressCompleted ?? 0}
              run={run}
            />
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
