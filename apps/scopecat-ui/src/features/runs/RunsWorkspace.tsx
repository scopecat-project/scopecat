import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  CircleDot,
  FlaskConical,
  ListFilter,
  LoaderCircle,
  Search,
  Unplug,
} from "lucide-react";
import {
  getMeasurementLivePreview,
  getMeasurementPreview,
  getMeasurementSlice,
  getMeasurementTracePreview,
  getOlderRunContents,
  getOlderRuns,
  getRun,
  getRunContents,
  getOlderRunAnalysisSummaries,
  getRunAnalysisSummaries,
  getRunDomainDecisions,
  getRunExecutionSegments,
  getRunEvents,
  getRuns,
  closeAttentionRun,
} from "./run-api";
import { errorMessage, formatRelative, shorten, titleCase } from "../../lib/presentation";
import type {
  MeasurementLivePreview,
  MeasurementPreview,
  ProjectHealth,
  ProjectRun,
  ProjectRunPage,
  RunAnalysisSummary,
} from "../../types";
import { useConfirmationDialog, type ConfirmationRequest } from "../../ui/ConfirmationDialog";
import { classes, eyebrow, secondaryButton } from "../../ui/styles";
import { RunDetail } from "./RunDetail";
import {
  measurementSlicePlan,
  measurementTraceQueryPlans,
  type MeasurementEntitySelection,
} from "./measurement-visualization";

type FilterKey = "all" | "active" | "attention" | "complete";
interface OlderRunHistory {
  headCursor: number;
  pages: ProjectRunPage[];
}
interface OlderRunPageRequest {
  headCursor: number;
  before: number;
}

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "attention", label: "Attention" },
  { key: "complete", label: "Finished" },
];

export function RunsWorkspace({
  selectedRunId,
  onSelectRun,
  health,
  healthPending,
  healthReachable,
  daemonUnavailable,
  onOpenSample,
}: {
  selectedRunId?: string;
  onSelectRun: (runId: string) => void;
  health?: ProjectHealth;
  healthPending: boolean;
  healthReachable: boolean;
  daemonUnavailable: boolean;
  onOpenSample?: (sampleId: string, revision: number) => void;
}) {
  const queryClient = useQueryClient();
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [olderRunHistory, setOlderRunHistory] = useState<OlderRunHistory>();
  const [requestedMeasurementSlice, setRequestedMeasurementSlice] = useState<{
    runId: string;
    fixedAxisIndices: Record<string, number>;
    offset: number;
  }>();
  const [requestedMeasurementTracePlan, setRequestedMeasurementTracePlan] = useState<{
    runId: string;
    planId: string;
  }>();
  const [requestedMeasurementEntities, setRequestedMeasurementEntities] = useState<{
    runId: string;
    selection: MeasurementEntitySelection;
  }>();
  const handleMeasurementEntitySelectionChange = useCallback(
    (selection: MeasurementEntitySelection) => {
      if (!selectedRunId) return;
      setRequestedMeasurementEntities((current) => {
        if (
          current?.runId === selectedRunId &&
          JSON.stringify(current.selection) === JSON.stringify(selection)
        ) {
          return current;
        }
        return { runId: selectedRunId, selection };
      });
    },
    [selectedRunId],
  );
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: ({ signal }) => getRuns(signal),
  });
  const runHeadCursor = runsQuery.data?.nextCursor;
  const olderRunsMutation = useMutation({
    mutationFn: ({ before }: OlderRunPageRequest) => getOlderRuns(before),
    onSuccess: (page, request) => {
      const latestHeadCursor = queryClient.getQueryData<ProjectRunPage>(["runs"])?.nextCursor;
      if (latestHeadCursor !== request.headCursor) return;
      setOlderRunHistory((current) =>
        current?.headCursor === request.headCursor
          ? { ...current, pages: [...current.pages, page] }
          : { headCursor: request.headCursor, pages: [page] },
      );
    },
  });
  const runDetailQuery = useQuery({
    queryKey: ["run", selectedRunId],
    queryFn: ({ signal }) => getRun(selectedRunId!, signal),
    enabled: selectedRunId !== undefined,
  });
  const runContentsQuery = useInfiniteQuery({
    queryKey: ["run-contents", selectedRunId],
    queryFn: ({ pageParam, signal }) =>
      pageParam === undefined
        ? getRunContents(selectedRunId!, signal)
        : getOlderRunContents(selectedRunId!, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.nextCursor,
    enabled: selectedRunId !== undefined,
  });
  const selectedEventsQuery = useQuery({
    queryKey: ["events", "run", selectedRunId],
    queryFn: ({ signal }) => getRunEvents(selectedRunId!, signal),
    enabled: selectedRunId !== undefined,
  });
  const measurementsQuery = useQuery({
    queryKey: ["measurements", selectedRunId],
    queryFn: ({ signal }) => getMeasurementPreview(selectedRunId!, signal),
    enabled: selectedRunId !== undefined,
  });
  const selectedRunStatus =
    runDetailQuery.data?.status ??
    runsQuery.data?.items.find((run) => run.runId === selectedRunId)?.status;
  const selectedRunIsActive =
    selectedRunStatus === undefined || ["accepted", "running"].includes(selectedRunStatus);
  const executionSegmentsQuery = useQuery({
    queryKey: ["run-execution-segments", selectedRunId],
    queryFn: ({ signal }) => getRunExecutionSegments(selectedRunId!, signal),
    enabled: selectedRunId !== undefined,
    refetchInterval: (query) =>
      selectedRunIsActive ||
      query.state.data?.items.some(
        (segment) => segment.ended_at === null || segment.ended_at === undefined,
      )
        ? 1000
        : false,
  });
  const runDomainDecisionsQuery = useQuery({
    queryKey: ["run-domain-decisions", selectedRunId],
    queryFn: ({ signal }) => getRunDomainDecisions(selectedRunId!, signal),
    enabled: selectedRunId !== undefined,
    refetchInterval: selectedRunIsActive ? 1000 : false,
  });
  const liveMeasurementQueryKey = ["measurements", "live", selectedRunId] as const;
  const liveMeasurementsQuery = useQuery({
    queryKey: liveMeasurementQueryKey,
    queryFn: async ({ signal }) => {
      const previous = queryClient.getQueryData<MeasurementLivePreview>(liveMeasurementQueryKey);
      const current = await getMeasurementLivePreview(
        selectedRunId!,
        signal,
        previous?.receivedRecordCount,
      );
      return current.active && current.latest === undefined && previous?.active
        ? { ...current, latest: previous.latest }
        : current;
    },
    enabled: selectedRunId !== undefined,
    refetchInterval: selectedRunIsActive ? 250 : false,
  });
  const analysesQuery = useInfiniteQuery({
    queryKey: ["analyses", "run", selectedRunId],
    queryFn: ({ pageParam, signal }) =>
      pageParam === undefined
        ? getRunAnalysisSummaries(selectedRunId!, signal)
        : getOlderRunAnalysisSummaries(selectedRunId!, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.nextCursor,
    enabled: selectedRunId !== undefined,
  });
  const attentionMutation = useMutation({
    mutationFn: (runId: string) => closeAttentionRun(runId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["events"] }),
        queryClient.invalidateQueries({ queryKey: ["run"] }),
        queryClient.invalidateQueries({ queryKey: ["run-contents"] }),
      ]);
    },
  });

  const olderRunPages = useMemo(
    () =>
      olderRunHistory && olderRunHistory.headCursor === runHeadCursor ? olderRunHistory.pages : [],
    [olderRunHistory, runHeadCursor],
  );
  const runs = useMemo(() => {
    const indexedRuns = mergeRunPages([
      ...olderRunPages,
      ...(runsQuery.data ? [runsQuery.data] : []),
    ]);
    return runDetailQuery.data
      ? indexedRuns.map((run) =>
          run.runId === runDetailQuery.data.runId ? runDetailQuery.data : run,
        )
      : indexedRuns;
  }, [olderRunPages, runDetailQuery.data, runsQuery.data]);
  const nextRunCursor = olderRunPages.length > 0 ? olderRunPages.at(-1)?.nextCursor : runHeadCursor;
  const measurements = useMemo(
    () => mergeMeasurementPreviews(measurementsQuery.data, liveMeasurementsQuery.data),
    [liveMeasurementsQuery.data, measurementsQuery.data],
  );
  const runAnalyses = useMemo(() => {
    const items = new Map<string, RunAnalysisSummary>();
    for (const page of analysesQuery.data?.pages ?? []) {
      for (const analysis of page.items) items.set(analysis.id, analysis);
    }
    return [...items.values()];
  }, [analysesQuery.data]);
  const runContents = useMemo(() => {
    const items = new Map<string, ProjectRun["contents"][number]>();
    for (const page of runContentsQuery.data?.pages ?? []) {
      for (const content of page.items) {
        items.set(`${content.role}:${content.id}`, content);
      }
    }
    return [...items.values()];
  }, [runContentsQuery.data]);
  const slicePlan = useMemo(
    () => measurementSlicePlan(measurements?.schema),
    [measurements?.schema],
  );
  const tracePlans = useMemo(
    () => measurementTraceQueryPlans(measurements?.schema),
    [measurements?.schema],
  );
  const requestedTracePlanId =
    requestedMeasurementTracePlan !== undefined &&
    requestedMeasurementTracePlan.runId === selectedRunId
      ? requestedMeasurementTracePlan.planId
      : undefined;
  const selectedTracePlan =
    tracePlans.find((plan) => plan.id === requestedTracePlanId) ?? tracePlans[0];
  const measurementFixedAxisIndices = useMemo(
    () =>
      Object.fromEntries(
        (slicePlan?.fixedAxes ?? []).map((axis) => {
          const requested =
            requestedMeasurementSlice?.runId === selectedRunId
              ? requestedMeasurementSlice?.fixedAxisIndices[axis.id]
              : undefined;
          return [axis.id, requested !== undefined && requested < axis.size ? requested : 0];
        }),
      ),
    [requestedMeasurementSlice, selectedRunId, slicePlan],
  );
  const measurementSliceKey = JSON.stringify(measurementFixedAxisIndices);
  const measurementSliceOffset =
    requestedMeasurementSlice !== undefined &&
    requestedMeasurementSlice.runId === selectedRunId &&
    JSON.stringify(requestedMeasurementSlice.fixedAxisIndices) === measurementSliceKey
      ? requestedMeasurementSlice.offset
      : 0;
  const currentMeasurementEntitySelection =
    requestedMeasurementEntities !== undefined &&
    requestedMeasurementEntities.runId === selectedRunId
      ? requestedMeasurementEntities.selection
      : {};
  const selectedTraceEntityIndices = selectedTracePlan?.entityAxisId
    ? currentMeasurementEntitySelection[selectedTracePlan.entityAxisId]
    : undefined;
  const traceEntityAxis = measurements?.schema?.dimensions.find(
    (dimension) => dimension.id === selectedTracePlan?.entityAxisId,
  );
  const traceEntityIndex =
    traceEntityAxis?.index?.kind === "entity" ? traceEntityAxis.index.values : undefined;
  const selectedTraceEntities = selectedTraceEntityIndices?.flatMap((index) =>
    traceEntityIndex?.[index] ? [traceEntityIndex[index]] : [],
  );
  const measurementTraceEntityKey = JSON.stringify(selectedTraceEntities ?? null);
  const measurementSliceQuery = useQuery({
    queryKey: ["measurement-slice", selectedRunId, measurementSliceKey, measurementSliceOffset],
    queryFn: ({ signal }) =>
      getMeasurementSlice(
        selectedRunId!,
        measurementFixedAxisIndices,
        slicePlan!.variableIds,
        measurementSliceOffset,
        signal,
      ),
    enabled: selectedRunId !== undefined && slicePlan !== undefined,
  });
  const measurementTraceQuery = useQuery({
    queryKey: [
      "measurement-trace",
      selectedRunId,
      selectedTracePlan?.id,
      measurementSliceKey,
      measurementTraceEntityKey,
    ],
    queryFn: ({ signal }) =>
      getMeasurementTracePreview(
        selectedRunId!,
        {
          observableId: selectedTracePlan!.observableId,
          ...(selectedTracePlan!.coordinateId
            ? { coordinateId: selectedTracePlan!.coordinateId }
            : {}),
          fixedAxisIndices: measurementFixedAxisIndices,
          valueMode: selectedTracePlan!.valueMode,
          ...(selectedTraceEntities ? { entities: selectedTraceEntities } : {}),
        },
        signal,
      ),
    enabled: selectedRunId !== undefined && selectedTracePlan !== undefined,
  });
  const filteredRuns = useMemo(() => filterRuns(runs, filter, search), [runs, filter, search]);

  useEffect(() => {
    if (runs.length > 0 && selectedRunId === undefined) {
      const firstRunId = runs[0]?.runId;
      if (firstRunId !== undefined) onSelectRun(firstRunId);
    }
  }, [runs, selectedRunId, onSelectRun]);

  const selectedRunBase = runDetailQuery.data ?? runs.find((run) => run.runId === selectedRunId);
  const selectedRun = selectedRunBase ? { ...selectedRunBase, contents: runContents } : undefined;
  const selectedEvents = selectedEventsQuery.data ?? [];
  const activeCount = runs.filter((run) => ["accepted", "running"].includes(run.status)).length;
  const attentionCount = runs.filter((run) => run.status === "attention_required").length;

  return (
    <>
      <section
        className="mb-2.5 grid grid-cols-3 overflow-hidden rounded-lg border border-line bg-panel max-[460px]:grid-cols-[minmax(0,1fr)]"
        aria-label="Project status"
      >
        <StatusItem
          label="Daemon"
          value={
            healthReachable
              ? titleCase(health?.status ?? "connected")
              : healthPending
                ? "Checking"
                : "Unavailable"
          }
          detail={healthDetail(health)}
          tone={healthReachable ? "good" : "muted"}
        />
        <StatusItem
          label="Runs"
          value={
            runsQuery.isSuccess ? `${runs.length}${nextRunCursor !== undefined ? "+" : ""}` : "—"
          }
          detail={
            runsQuery.isSuccess
              ? nextRunCursor !== undefined
                ? `${activeCount} active in loaded runs`
                : `${activeCount} active`
              : "No run data received"
          }
          tone={activeCount > 0 ? "active" : "muted"}
        />
        <StatusItem
          label="Attention"
          value={runsQuery.isSuccess ? String(attentionCount) : "—"}
          detail={
            attentionCount > 0
              ? "Operator review needed"
              : nextRunCursor !== undefined
                ? "No flags in loaded runs"
                : "No flagged runs"
          }
          tone={attentionCount > 0 ? "warning" : nextRunCursor !== undefined ? "muted" : "good"}
        />
      </section>

      <div className="grid min-h-[620px] grid-cols-[minmax(300px,340px)_minmax(0,1fr)] items-start overflow-hidden rounded-lg border border-line bg-panel max-[1100px]:grid-cols-[minmax(270px,310px)_minmax(0,1fr)] max-[880px]:block max-[880px]:min-h-0 max-[880px]:overflow-visible max-[880px]:bg-transparent">
        <aside
          className="sticky top-[60px] flex max-h-[calc(100vh-72px)] min-h-[620px] flex-col border-r border-line bg-panel-soft px-[11px] pt-[13px] pb-[11px] max-[880px]:static max-[880px]:mb-3 max-[880px]:max-h-none max-[880px]:min-h-0 max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line max-[680px]:px-[11px] max-[680px]:pt-4 max-[680px]:pb-[11px]"
          aria-labelledby="runs-heading"
        >
          <div className="flex items-start justify-between px-1 pb-[11px]">
            <div>
              <p className={eyebrow}>Run browser</p>
              <h2 className="m-0 text-[1.08rem] font-[650] tracking-[-0.02em]" id="runs-heading">
                Experiments
              </h2>
            </div>
            {runsQuery.isFetching && (
              <LoaderCircle
                className="mt-[7px] animate-spin text-text-dim"
                size={17}
                aria-label="Refreshing runs"
              />
            )}
          </div>

          <label className="flex min-h-10 items-center gap-[9px] rounded-[9px] border border-line bg-bg px-[11px] text-text-dim transition-[border-color,box-shadow] duration-150 focus-within:border-[rgb(128_163_207_/_55%)] focus-within:shadow-[0_0_0_3px_rgb(128_163_207_/_7%)]">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">Search by run, experiment, or resource</span>
            <input
              className="w-full min-w-0 border-0 bg-transparent p-0 text-[0.8rem] text-text outline-none placeholder:text-[#5e6a77]"
              type="search"
              placeholder="Search runs"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>

          <div
            className="my-2.5 mb-[11px] flex items-center gap-1 text-text-dim max-[460px]:overflow-x-auto"
            aria-label="Filter runs"
          >
            <ListFilter className="mx-[5px] ml-0.5 flex-none" size={15} aria-hidden="true" />
            {FILTERS.map((item) => (
              <button
                key={item.key}
                className={classes(
                  "min-h-7 cursor-pointer rounded-[7px] border border-transparent bg-transparent px-2 text-[0.69rem] font-bold text-text-dim hover:bg-[rgb(255_255_255_/_3%)] hover:text-text-soft",
                  filter === item.key &&
                    "border-[rgb(128_163_207_/_17%)] bg-accent-soft text-accent",
                )}
                type="button"
                aria-pressed={filter === item.key}
                onClick={() => setFilter(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-[7px] overflow-auto p-0.5 [scrollbar-color:#344252_transparent] [scrollbar-width:thin] max-[880px]:grid max-[880px]:grid-flow-col max-[880px]:auto-cols-[minmax(270px,64vw)] max-[880px]:overflow-x-auto max-[880px]:pb-[5px] max-[460px]:auto-cols-[minmax(260px,86vw)]">
            {runsQuery.isPending && (
              <PanelMessage
                icon={<LoaderCircle className="animate-spin" />}
                title="Reading project"
                detail="Waiting for the daemon to return its run index."
              />
            )}
            {runsQuery.isError && (
              <PanelMessage
                icon={<Unplug />}
                title="Run list unavailable"
                detail={errorMessage(runsQuery.error)}
              />
            )}
            {runsQuery.isSuccess && runs.length === 0 && (
              <PanelMessage
                icon={<FlaskConical />}
                title="No runs yet"
                detail="Submitted experiments will appear here."
              />
            )}
            {runsQuery.isSuccess && runs.length > 0 && filteredRuns.length === 0 && (
              <PanelMessage
                icon={<Search />}
                title="No matching runs"
                detail="Try another status or search term."
              />
            )}
            {filteredRuns.map((run) => (
              <RunListItem
                key={run.runId}
                run={run}
                selected={run.runId === selectedRunId}
                onSelect={() => onSelectRun(run.runId)}
              />
            ))}
            {olderRunsMutation.isError && (
              <p className="mx-2 mt-1 mb-0 text-[0.67rem] leading-[1.45] text-red" role="status">
                {errorMessage(olderRunsMutation.error)}
              </p>
            )}
            {runsQuery.isSuccess && runHeadCursor !== undefined && nextRunCursor !== undefined && (
              <div className="flex justify-center pt-[7px] pb-[3px]">
                <button
                  className={classes(secondaryButton, "w-full")}
                  type="button"
                  disabled={olderRunsMutation.isPending}
                  onClick={() =>
                    olderRunsMutation.mutate({
                      headCursor: runHeadCursor,
                      before: nextRunCursor,
                    })
                  }
                >
                  {olderRunsMutation.isPending ? (
                    <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
                  ) : (
                    <ChevronRight size={14} aria-hidden="true" />
                  )}
                  {olderRunsMutation.isPending ? "Loading older runs…" : "Load older runs"}
                </button>
              </div>
            )}
          </div>
        </aside>

        <section
          className="min-h-[620px] min-w-0 bg-panel p-[clamp(18px,2vw,26px)] max-[880px]:min-h-[580px] max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line max-[680px]:px-3.5 max-[680px]:py-5"
          aria-label="Selected run details"
        >
          {daemonUnavailable ? (
            <DetailEmpty
              icon={<Unplug />}
              title="Connect to the local daemon"
              detail="Project status and run data are read directly from the daemon. This console does not maintain an offline copy."
            />
          ) : selectedRun ? (
            <RunDetail
              run={selectedRun}
              events={selectedEvents}
              eventsError={selectedEventsQuery.error}
              eventsPending={selectedEventsQuery.isPending}
              executionSegments={executionSegmentsQuery.data}
              executionSegmentsError={executionSegmentsQuery.error}
              executionSegmentsPending={executionSegmentsQuery.isPending}
              domainDecisions={runDomainDecisionsQuery.data}
              domainDecisionsError={runDomainDecisionsQuery.error}
              domainDecisionsPending={runDomainDecisionsQuery.isPending}
              measurements={measurements}
              measurementsError={measurementsQuery.error}
              measurementsPending={measurementsQuery.isPending}
              measurementSlice={measurementSliceQuery.data}
              measurementSliceError={measurementSliceQuery.error}
              measurementSlicePending={measurementSliceQuery.isFetching}
              tracePlans={tracePlans}
              selectedTracePlanId={selectedTracePlan?.id}
              tracePreview={measurementTraceQuery.data}
              traceError={measurementTraceQuery.error}
              tracePending={measurementTraceQuery.isFetching}
              onTracePlanChange={(planId) => {
                setRequestedMeasurementTracePlan({
                  runId: selectedRunId!,
                  planId,
                });
              }}
              onMeasurementEntitySelectionChange={handleMeasurementEntitySelectionChange}
              measurementFixedAxisIndices={measurementFixedAxisIndices}
              onMeasurementSliceOffsetChange={(offset) => {
                setRequestedMeasurementSlice({
                  runId: selectedRunId!,
                  fixedAxisIndices: measurementFixedAxisIndices,
                  offset,
                });
              }}
              onMeasurementFixedAxisIndexChange={(axisId, index) => {
                setRequestedMeasurementSlice({
                  runId: selectedRunId!,
                  fixedAxisIndices: {
                    ...measurementFixedAxisIndices,
                    [axisId]: index,
                  },
                  offset: 0,
                });
              }}
              analyses={runAnalyses}
              analysesError={analysesQuery.error}
              analysesPending={analysesQuery.isPending}
              analysesHasNextPage={analysesQuery.hasNextPage}
              analysesLoadingNextPage={analysesQuery.isFetchingNextPage}
              onLoadOlderAnalyses={() => void analysesQuery.fetchNextPage()}
              contentsError={runContentsQuery.error}
              contentsPending={runContentsQuery.isPending}
              contentsHasNextPage={runContentsQuery.hasNextPage}
              contentsLoadingNextPage={runContentsQuery.isFetchingNextPage}
              onLoadOlderContents={() => void runContentsQuery.fetchNextPage()}
              attentionError={attentionMutation.error}
              attentionPending={attentionMutation.isPending}
              onResolveAttention={() => {
                const confirmation = attentionConfirmation();
                requestConfirmation({
                  ...confirmation,
                  onConfirm: () => attentionMutation.mutate(selectedRun.runId),
                });
              }}
              onOpenSample={onOpenSample}
            />
          ) : runsQuery.isPending ? (
            <DetailEmpty
              icon={<LoaderCircle className="animate-spin" />}
              title="Loading run details"
              detail="The project run index is being read."
            />
          ) : (
            <DetailEmpty
              icon={<CircleDot />}
              title="No run selected"
              detail="Choose a run to inspect its progress, resource claims, events, and data contents."
            />
          )}
        </section>
      </div>
      {confirmationDialog}
    </>
  );
}

function StatusItem({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: "good" | "active" | "warning" | "muted";
}) {
  return (
    <article className="grid min-h-11 min-w-0 grid-cols-[auto_auto_minmax(0,1fr)] items-baseline gap-[9px] border-r border-line px-3 py-2 last:border-r-0 max-[680px]:grid-cols-[auto_auto] max-[680px]:[&>small]:col-span-full max-[460px]:border-r-0 max-[460px]:not-first:border-t max-[460px]:not-first:border-line">
      <span className="text-[0.59rem] font-extrabold tracking-[0.1em] text-text-dim uppercase">
        {label}
      </span>
      <strong
        className={classes(
          "overflow-hidden text-[0.74rem] font-[650] tracking-[-0.02em] text-ellipsis whitespace-nowrap",
          statusTone[tone],
        )}
      >
        {value}
      </strong>
      <small className="overflow-hidden text-[0.61rem] text-ellipsis whitespace-nowrap text-text-dim">
        {detail}
      </small>
    </article>
  );
}

function mergeMeasurementPreviews(
  durable: MeasurementPreview | undefined,
  live: MeasurementLivePreview | undefined,
): MeasurementPreview | undefined {
  const latest = live?.active ? live.latest : undefined;
  if (!durable && !latest) return undefined;
  const durableItems = durable?.items ?? [];
  const items =
    latest && !durableItems.some((record) => record.point_index === latest.point_index)
      ? [...durableItems, latest]
      : durableItems;
  const livePointIndex =
    live && latest && live.receivedRecordCount > live.durableRecordCount
      ? latest.point_index
      : undefined;
  return {
    ...durable,
    items,
    recordCount: live?.active ? live.receivedRecordCount : durable?.recordCount,
    durableRecordCount: live?.active ? live.durableRecordCount : durable?.durableRecordCount,
    livePointIndex,
  };
}

function RunListItem({
  run,
  selected,
  onSelect,
}: {
  run: ProjectRun;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={classes(
        "relative grid min-h-[76px] w-full cursor-pointer grid-cols-[9px_minmax(0,1fr)_16px] items-start gap-[9px] rounded-md border border-transparent bg-transparent p-[9px] text-left text-text transition-[border-color,background] duration-150 hover:border-line hover:bg-[rgb(255_255_255_/_2%)]",
        selected && "border-line-strong bg-panel-strong",
      )}
      data-testid="run-list-item"
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      title={`Inspect run ${run.runId}`}
    >
      <span
        className={classes("mt-[5px] size-2 rounded-full", runStatusDot[run.status])}
        aria-hidden
      />
      <span className="grid min-w-0">
        <span className="flex min-w-0 items-baseline justify-between gap-2">
          <strong className="overflow-hidden text-[0.8rem] font-[650] text-ellipsis whitespace-nowrap">
            {run.displayName ?? run.experimentId}
          </strong>
          <time className="flex-none text-[0.62rem] text-text-dim" dateTime={run.updatedAt}>
            {run.updatedAt ? formatRelative(run.updatedAt) : "No timestamp"}
          </time>
        </span>
        <span className="mt-1.5 flex items-center gap-[5px] overflow-hidden text-[0.67rem] whitespace-nowrap text-text-dim">
          {run.displayName && (
            <>
              <code className="overflow-hidden text-ellipsis text-text-soft">
                {run.experimentId}
              </code>
              <span aria-hidden="true">·</span>
            </>
          )}
          <code className="overflow-hidden text-ellipsis text-text-soft">
            {shorten(run.runId, 18)}
          </code>
        </span>
        <span className="mt-2 flex flex-wrap items-center gap-1.5">
          <span
            className={classes(
              "rounded-[5px] border px-1.5 py-[3px] text-[0.6rem] leading-none font-extrabold tracking-[0.05em] uppercase",
              runStatusBadge[run.status],
            )}
          >
            {run.stateLabel}
          </span>
          {run.samples.slice(0, 2).map((sample) => (
            <span
              key={`${sample.role}:${sample.sample_id}`}
              className="rounded-[5px] border border-[rgb(128_163_207_/_18%)] bg-accent-soft px-1.5 py-[3px] text-[0.59rem] leading-none text-accent"
              title={`${sample.role}: ${sample.sample_id} revision ${sample.revision}`}
            >
              {sample.display_name}
            </span>
          ))}
        </span>
      </span>
      <ChevronRight
        size={16}
        className={classes("self-center text-text-dim", selected && "text-accent")}
        aria-hidden="true"
      />
    </button>
  );
}

function PanelMessage({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <div className="grid justify-items-center px-5 py-11 text-center text-text-dim max-[880px]:min-w-[280px] max-[880px]:px-[18px] max-[880px]:py-7">
      <span
        className="mb-3 grid size-[38px] place-items-center rounded-[10px] border border-line bg-panel text-text-soft [&>svg]:w-[18px]"
        aria-hidden="true"
      >
        {icon}
      </span>
      <strong className="text-[0.78rem] text-text-soft">{title}</strong>
      <p className="mt-1.5 mb-0 max-w-[230px] text-[0.7rem] leading-normal">{detail}</p>
    </div>
  );
}

function DetailEmpty({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <div className="grid min-h-[590px] place-content-center justify-items-center text-center max-[880px]:min-h-[500px]">
      <span
        className="mb-4 grid size-[55px] place-items-center rounded-[14px] border border-line bg-panel-soft text-text-soft [&>svg]:w-[23px]"
        aria-hidden="true"
      >
        {icon}
      </span>
      <h2 className="m-0 text-base">{title}</h2>
      <p className="mt-2 mb-0 max-w-[410px] text-[0.73rem] leading-[1.55] text-text-dim">
        {detail}
      </p>
    </div>
  );
}

const statusTone: Record<"good" | "active" | "warning" | "muted", string> = {
  good: "text-accent",
  active: "text-blue",
  warning: "text-yellow",
  muted: "",
};

const runStatusDot: Record<ProjectRun["status"], string> = {
  accepted: "bg-blue",
  running: "bg-accent",
  attention_required: "bg-yellow",
  succeeded: "bg-accent",
  failed: "bg-red",
  cancelled: "bg-red",
};

const runStatusBadge: Record<ProjectRun["status"], string> = {
  accepted: "border-[rgb(120_184_255_/_20%)] bg-blue-soft text-blue",
  running: "border-[rgb(128_163_207_/_20%)] bg-accent-soft text-accent",
  attention_required: "border-[rgb(237_201_111_/_20%)] bg-yellow-soft text-yellow",
  succeeded: "border-[rgb(128_163_207_/_20%)] bg-accent-soft text-accent",
  failed: "border-[rgb(255_140_136_/_20%)] bg-red-soft text-red",
  cancelled: "border-[rgb(255_140_136_/_20%)] bg-red-soft text-red",
};

function mergeRunPages(pages: ProjectRunPage[]): ProjectRun[] {
  const runs = new Map<string, ProjectRun>();
  for (const page of pages) {
    for (const run of page.items) runs.set(run.runId, run);
  }
  return [...runs.values()].sort((left, right) => {
    if (left.sequence !== undefined && right.sequence !== undefined) {
      return right.sequence - left.sequence;
    }
    return (right.updatedAt ?? "").localeCompare(left.updatedAt ?? "");
  });
}

function filterRuns(runs: ProjectRun[], filter: FilterKey, search: string): ProjectRun[] {
  const query = search.trim().toLocaleLowerCase();
  return runs.filter((run) => {
    const matchesFilter =
      filter === "all" ||
      (filter === "active" && ["accepted", "running"].includes(run.status)) ||
      (filter === "attention" && run.status === "attention_required") ||
      (filter === "complete" && ["succeeded", "failed", "cancelled"].includes(run.status));
    if (!matchesFilter) return false;
    if (!query) return true;
    return [
      run.runId,
      run.experimentId,
      run.displayName,
      ...run.tags,
      ...run.samples.flatMap((sample) => [
        sample.sample_id,
        sample.display_name,
        sample.kind,
        sample.role,
        sample.context_id,
      ]),
      ...run.resources.flatMap((resource) => [resource.id, resource.kind]),
    ]
      .filter((value) => value !== undefined)
      .join(" ")
      .toLocaleLowerCase()
      .includes(query);
  });
}

function healthDetail(health?: ProjectHealth): string {
  if (!health) return "No health response";
  return `Project ${shorten(health.projectId, 18)}`;
}

function attentionConfirmation(): Omit<ConfirmationRequest, "onConfirm"> {
  return {
    title: "Close without resuming this run?",
    description:
      "Confirm the external hardware state is reconciled. This releases its resources and makes the run terminal.",
    confirmLabel: "Close run",
    intent: "danger",
  };
}
