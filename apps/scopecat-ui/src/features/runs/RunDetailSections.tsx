import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Cpu,
  Database,
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
} from "../../types";
import { MeasurementWorkspace } from "./MeasurementWorkspace";
import { MeasurementDataPreview } from "./MeasurementDataPreview";
import { canPreviewRunContent, getRunContent } from "./run-api";
import type {
  MeasurementSliceSelection,
  MeasurementEntitySelection,
  MeasurementTraceQueryPlan,
} from "./measurement-visualization";

export function ProgressCard({
  run,
  measurements,
  receiptError,
}: {
  run: ProjectRun;
  events: ProjectEvent[];
  measurements?: MeasurementPreview;
  receiptError?: Error | null;
}) {
  const received = measurements?.recordCount;
  const saved = measurements?.durableRecordCount;
  const savedPreview = measurements
    ? `${measurements.items.length}${measurements.truncated ? "+" : ""}`
    : undefined;

  const active = ["accepted", "running"].includes(run.status);
  return (
    <section
      aria-label="run 进度"
      className="rounded-md border border-line bg-panel-soft px-4 py-2 text-sm"
    >
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <strong>{run.stateLabel}</strong>
        <span>
          已完成逻辑点 <strong>{run.completedLogicalPointCount ?? "未知"}</strong>
          {" ／ 计划点 "}
          <strong>{run.plan.pointCount ?? "未知"}</strong>
        </span>
        {received !== undefined && (
          <span>
            已接收记录 <strong className="font-mono text-text">{received}</strong>
          </span>
        )}
        {saved !== undefined && (
          <span>
            已保存记录 <strong className="font-mono text-text">{saved}</strong>
          </span>
        )}
        {saved === undefined && savedPreview !== undefined && (
          <span>
            已保存预览记录 <strong className="font-mono text-text">{savedPreview}</strong>
          </span>
        )}
        <span className="text-text-soft">
          {run.plan.pointCount !== undefined
            ? `${run.plan.pointCount} 个计划点`
            : `已接纳 ${run.pointPlan.acceptedPointCount} 个计划点 · 计划${run.pointPlan.closed ? "已封闭" : "仍开放"}`}
        </span>
      </div>
      {receiptError && (
        <p role="status" className="mt-2 text-sm text-yellow">
          实时更新不可用，当前计数与曲线可能已过时： {errorMessage(receiptError)}
        </p>
      )}
      {received !== undefined && saved !== undefined && received > saved && (
        <p className="mt-2 text-sm text-yellow">还有 {received - saved} 条已接收记录尚未保存。</p>
      )}
      {received === undefined && active && (
        <p className="mt-2 text-sm text-text-soft">
          {run.status === "accepted" ? "等待开始，尚未收到测量数据。" : "尚未收到测量数据。"}
        </p>
      )}
      <details className="mt-1 text-xs text-text-dim">
        <summary className="cursor-pointer">执行证据</summary>
        <p className="mt-2">
          连续完成前缀 {run.progressCompleted ?? "未知"}{" "}
          个逻辑点；已接收记录、已保存记录与逻辑点分别计数。
        </p>
        <p className="mt-1">
          运行结果：{progressResultLabel(run.result)} · 结果确定性：
          {progressCertaintyLabel(run.certainty)} · 运行更新时间：
          {run.updatedAt ? formatRelative(run.updatedAt) : "尚未报告"}
        </p>
        <p className="mt-1">
          接收记录数表示后台服务已收到的数据，不代表设备批次内部的执行进度，
          也不据此推断设备采集进度。运行成功还要求完成数据保存与收尾。
        </p>
      </details>
    </section>
  );
}

function progressResultLabel(result?: string): string {
  switch (result) {
    case undefined:
      return "尚无结果";
    case "succeeded":
      return "成功";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    default:
      return result;
  }
}

function progressCertaintyLabel(certainty?: string): string {
  switch (certainty) {
    case undefined:
      return "尚未报告";
    case "known":
      return "已确定";
    case "indeterminate":
      return "无法确定";
    default:
      return certainty;
  }
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
        <strong className="text-xs text-text-soft">Segment {segment.ordinal + 1}</strong>
        <span
          className={classes(
            "rounded-[5px] border px-[5px] py-[3px] text-xs font-extrabold uppercase",
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
      <p className="m-0 text-xs leading-[1.45] text-text-dim">
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
                <strong className="overflow-hidden text-xs font-[650] text-ellipsis whitespace-nowrap">
                  {resource.id}
                </strong>
                <small className="overflow-hidden text-xs text-ellipsis whitespace-nowrap text-text-dim">
                  {titleCase(resource.kind)}
                </small>
                {resource.blockedBy && (
                  <small className="break-all text-xs text-yellow">
                    Blocked by{" "}
                    {resource.blockedBy.ownerKind === "run" ? "run" : "interactive session"}{" "}
                    {resource.blockedBy.ownerId}
                    {resource.blockedBy.status === "quarantined" && " — reconciliation required"}
                  </small>
                )}
              </span>
              <span
                className={classes(
                  "flex-none rounded-[5px] border px-[5px] py-[3px] text-xs font-extrabold uppercase",
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
            <p className="mt-0 mb-3 text-xs leading-[1.45] text-text-dim">
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
                    <strong className="text-xs font-[650]">{humanizeEvent(event.kind)}</strong>
                    <span className="font-mono text-xs text-text-dim">#{event.id}</span>
                  </div>
                  <p className="my-1 text-xs leading-[1.45] text-text-dim">
                    {eventDescription(event)}
                  </p>
                  <time className="text-xs text-text-dim" dateTime={event.occurredAt}>
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
  progress,
  notices,
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
  traceRefreshing,
  onTracePlanChange,
  onMeasurementEntitySelectionChange,
  measurementFixedAxisIndices,
  measurementAxes,
  onMeasurementAxesChange,
  onMeasurementSliceOffsetChange,
  onMeasurementFixedAxisIndexChange,
}: {
  run: ProjectRun;
  progress?: ReactNode;
  notices?: ReactNode;
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
  traceRefreshing?: boolean;
  onTracePlanChange: (planId: string) => void;
  onMeasurementEntitySelectionChange: (selection: MeasurementEntitySelection) => void;
  measurementFixedAxisIndices: Record<string, number>;
  measurementAxes?: MeasurementSliceSelection;
  onMeasurementAxesChange?: (selection: MeasurementSliceSelection) => void;
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
    <article className="min-w-0 [&>.run-inline-empty]:mt-2.5" data-testid="data-card">
      <MeasurementWorkspace
        key={run.runId}
        title={run.displayName ?? run.experimentId}
        progress={progress}
        notices={notices}
      >
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
          traceRefreshing={traceRefreshing}
          onTracePlanChange={onTracePlanChange}
          onMeasurementEntitySelectionChange={onMeasurementEntitySelectionChange}
          fixedAxisIndices={measurementFixedAxisIndices}
          measurementAxes={measurementAxes}
          onMeasurementAxesChange={onMeasurementAxesChange}
          onSliceOffsetChange={onMeasurementSliceOffsetChange}
          onFixedAxisIndexChange={onMeasurementFixedAxisIndexChange}
        />
      </MeasurementWorkspace>
      <details className="mt-4 rounded-md border border-line p-3">
        <summary className="cursor-pointer text-sm font-semibold text-text-soft">
          已保存文件与记录元数据
        </summary>
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
            <Fact label="Record types" value={String(run.plan.recordIds.length)} />
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
                      <strong className="overflow-hidden text-xs font-[650] text-ellipsis whitespace-nowrap">
                        {content.label}
                      </strong>
                      <small className="overflow-hidden text-xs text-ellipsis whitespace-nowrap text-text-dim">
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
              <p className="m-0 text-xs text-red" role="status">
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
      </details>
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
        <strong className="text-xs text-text-soft">{entry.label}</strong>
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
  traceRefreshing,
  onTracePlanChange,
  onMeasurementEntitySelectionChange,
  fixedAxisIndices,
  measurementAxes,
  onMeasurementAxesChange,
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
  traceRefreshing?: boolean;
  onTracePlanChange: (planId: string) => void;
  onMeasurementEntitySelectionChange: (selection: MeasurementEntitySelection) => void;
  fixedAxisIndices: Record<string, number>;
  measurementAxes?: MeasurementSliceSelection;
  onMeasurementAxesChange?: (selection: MeasurementSliceSelection) => void;
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
      traceRefreshing={traceRefreshing}
      onTracePlanChange={onTracePlanChange}
      onEntitySelectionChange={onMeasurementEntitySelectionChange}
      fixedAxisIndices={fixedAxisIndices}
      measurementAxes={measurementAxes}
      onMeasurementAxesChange={onMeasurementAxesChange}
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
      <h3 className="m-0 text-sm font-bold">{title}</h3>
      {accessory && <div className="text-xs text-text-dim">{accessory}</div>}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid min-w-0 gap-1">
      <span className="text-xs font-bold tracking-[0.05em] text-text-dim uppercase">{label}</span>
      <strong className="overflow-hidden text-xs font-[650] text-ellipsis whitespace-nowrap text-text-soft">
        {value}
      </strong>
    </div>
  );
}

function TagGroup({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="mb-3">
      <span className="mb-1.5 block text-xs font-extrabold tracking-[0.06em] text-text-dim uppercase">
        {label}
      </span>
      <div className="flex flex-wrap gap-[5px]">
        {values.map((value) => (
          <code
            className="rounded-[5px] border border-line bg-[rgb(182_156_255_/_7%)] px-1.5 py-1 text-xs text-purple"
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
        <strong className="block text-xs text-text-soft">{title}</strong>
        <p className="mt-1 mb-0 text-xs leading-[1.45]">{detail}</p>
      </div>
    </div>
  );
}

const previewPanel = "mt-3 overflow-hidden rounded-[8px] border border-line bg-panel";
const previewHeading =
  "flex items-center justify-between border-b border-line px-2.5 py-2 text-xs text-text-dim";
const previewContent =
  "m-0 max-h-[260px] overflow-auto p-2.5 text-xs leading-normal text-text-soft [scrollbar-color:#344252_transparent] [scrollbar-width:thin]";

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
