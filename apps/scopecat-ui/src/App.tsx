import {
  WorkspaceNavigation,
  projectViewLabel,
  type ProjectView,
} from "./features/application/WorkspaceNavigation";
import { useWorkspacePosition } from "./features/application/use-workspace-position";
import { PinnedTask } from "./features/launch/PinnedTask";
import { getWaitingProcedures } from "./features/decisions/decision-api";
import "./workspace.css";
import type {
  ObjectParameterContext,
  ObjectParameterPanel,
  ParameterWorkspaceHandle,
} from "./features/config/object-parameters";
import type { ComparisonHandoff } from "./features/analyses/RunComparison";
import {
  Activity as RetainedView,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RefreshCw, Unplug } from "lucide-react";
import { parameterProposalKeys } from "./data/parameter-proposals/query-keys";
import { getEvents, getHealth } from "./data/project-api";
import { LaunchDraftProvider, useLaunchDraft } from "./features/launch/LaunchDraft";
import { RunsWorkspace } from "./features/runs/RunsWorkspace";
import { DesktopSession } from "./features/application/DesktopSession";
import { DesktopFiles } from "./features/application/DesktopFiles";
import { DesktopZoom } from "./features/application/DesktopZoom";
import { DesktopFind } from "./features/application/DesktopFind";
import {
  navigate,
  navigateBack,
  navigateForward,
  useLocationUrl,
  type NavigationOptions,
} from "./lib/navigation";
import { classes, iconButton } from "./ui/styles";

const HelpWorkspace = lazy(async () => {
  const module = await import("./features/help/HelpWorkspace");
  return { default: module.HelpWorkspace };
});

const ApplicationSettings = lazy(async () => {
  const module = await import("./features/application/ApplicationSettings");
  return { default: module.ApplicationSettings };
});

const LaunchWorkspace = lazy(async () => {
  const module = await import("./features/launch/LaunchWorkspace");
  return { default: module.LaunchWorkspace };
});

const AnalysesWorkspace = lazy(async () => {
  const module = await import("./features/analyses/AnalysesWorkspace");
  return { default: module.AnalysesWorkspace };
});

const ResearchHistory = lazy(async () => {
  const module = await import("./features/history/ResearchHistory");
  return { default: module.ResearchHistory };
});

const SamplesWorkspace = lazy(async () => {
  const module = await import("./features/samples/SamplesWorkspace");
  return { default: module.SamplesWorkspace };
});

const ConfigWorkspace = lazy(async () => {
  const module = await import("./features/config/ConfigWorkspace");
  return { default: module.ConfigWorkspace };
});

const InstrumentsWorkspace = lazy(async () => {
  const module = await import("./features/instruments/InstrumentsWorkspace");
  return { default: module.InstrumentsWorkspace };
});

const ReviewWorkspace = lazy(async () => {
  const module = await import("./features/reviews/ReviewWorkspace");
  return { default: module.ReviewWorkspace };
});

const DecisionWorkspace = lazy(async () => {
  const module = await import("./features/decisions/DecisionWorkspace");
  return { default: module.DecisionWorkspace };
});

export default function App() {
  const queryClient = useQueryClient();
  const activeQueries = useIsFetching();
  const [handoff, setHandoff] = useState<{
    projectId: string | undefined;
    value: ComparisonHandoff;
  }>();
  const parameterWorkspace = useRef<ParameterWorkspaceHandle>(null);
  const [objectPanel, setObjectPanel] = useState<ObjectParameterPanel | null>(null);
  const editObjectParameters = useCallback(async (context: ObjectParameterContext) => {
    if (!parameterWorkspace.current) throw new Error("参数工作区仍在加载，请稍后重试。");
    await parameterWorkspace.current.openObject(context);
  }, []);
  const [view, setView] = useState<ProjectView>(projectViewFromLocation);
  const focused = useLocationUrl().searchParams.get("focus") === "run";
  useWorkspacePosition(view);
  const [selectedRunId, setSelectedRunId] = useState<string | undefined>(selectedRunFromUrl);
  const [selectedAnalysisId, setSelectedAnalysisId] = useState<string | undefined>(
    selectedAnalysisFromUrl,
  );
  const [selectedSampleId, setSelectedSampleId] = useState<string | undefined>(
    selectedSampleFromUrl,
  );
  const [selectedSampleRevision, setSelectedSampleRevision] = useState<number | undefined>(
    selectedSampleRevisionFromUrl,
  );
  const eventCursor = useRef(0);

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: 5_000,
  });
  const healthWasUnavailable = useRef(false);
  useEffect(() => {
    if (healthQuery.isError) healthWasUnavailable.current = true;
    if (!healthQuery.isSuccess || !healthWasUnavailable.current) return;
    healthWasUnavailable.current = false;
    let cancelled = false;
    const projectQueries = {
      predicate: (query: { queryKey: readonly unknown[] }) => query.queryKey[0] !== "health",
    };
    // Invalidation alone can reuse an unfinished first request (no cached data).
    // Retire requests from the old backend before refreshing the mounted page.
    void queryClient.cancelQueries(projectQueries).then(() => {
      if (!cancelled) void queryClient.invalidateQueries(projectQueries);
    });
    return () => {
      cancelled = true;
    };
  }, [healthQuery.isError, healthQuery.isSuccess, queryClient]);

  const eventsQuery = useQuery({
    queryKey: ["events"],
    queryFn: ({ signal }) => getEvents(signal),
  });

  useEffect(() => {
    const latest = eventsQuery.data?.at(-1)?.id;
    if (latest !== undefined) {
      eventCursor.current = Math.max(eventCursor.current, latest);
    }
  }, [eventsQuery.data]);

  useEffect(() => {
    const restoreHashRoute = () => {
      const nextView = projectViewFromLocation();
      setView(nextView);
      // A route describes its own page. Missing parameters on another page
      // must not erase this window's last selection when returning via navigation.
      if (nextView === "runs") setSelectedRunId(selectedRunFromUrl());
      if (nextView === "analyses") setSelectedAnalysisId(selectedAnalysisFromUrl());
      if (nextView === "samples") {
        setSelectedSampleId(selectedSampleFromUrl());
        setSelectedSampleRevision(selectedSampleRevisionFromUrl());
      }
    };
    window.addEventListener("hashchange", restoreHashRoute);
    window.addEventListener("popstate", restoreHashRoute);
    window.addEventListener("scopecat:back", navigateBack);
    window.addEventListener("scopecat:forward", navigateForward);
    return () => {
      window.removeEventListener("hashchange", restoreHashRoute);
      window.removeEventListener("popstate", restoreHashRoute);
      window.removeEventListener("scopecat:back", navigateBack);
      window.removeEventListener("scopecat:forward", navigateForward);
    };
  }, []);

  useEffect(() => {
    if (!healthQuery.isSuccess) return;
    const events = new EventSource(`/api/v1/events/stream?after=${eventCursor.current}`);
    let refreshTimer: number | undefined;
    const measurementRunsToRefresh = new Set<string>();
    const invalidateCanonicalQueries = () => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["events"] });
      void queryClient.invalidateQueries({ queryKey: ["run"] });
      void queryClient.invalidateQueries({ queryKey: ["analyses"] });
      void queryClient.invalidateQueries({ queryKey: ["samples"] });
      void queryClient.invalidateQueries({ queryKey: ["sample"] });
      void queryClient.invalidateQueries({ queryKey: ["sample-revisions"] });
      void queryClient.invalidateQueries({ queryKey: ["run-contents"] });
      void queryClient.invalidateQueries({ queryKey: ["run-content"] });
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
      void queryClient.invalidateQueries({ queryKey: ["reviews"] });
      void queryClient.invalidateQueries({ queryKey: ["review"] });
      void queryClient.invalidateQueries({ queryKey: ["procedure-decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["procedure-decision-steps"] });
      void queryClient.invalidateQueries({
        queryKey: parameterProposalKeys.all,
      });
    };
    const refreshAfterConnection = () => {
      void queryClient.invalidateQueries({ queryKey: ["experiment-launcher"] });
      invalidateCanonicalQueries();
      void queryClient.resetQueries({ queryKey: ["measurements"] });
      void queryClient.resetQueries({ queryKey: ["measurement-slice"] });
      void queryClient.resetQueries({ queryKey: ["measurement-trace"] });
    };
    const refresh = (event: Event) => {
      const measurementRunId = measurementEventRunId(event);
      if (measurementRunId !== undefined) {
        measurementRunsToRefresh.add(measurementRunId);
      }
      if (refreshTimer !== undefined) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined;
        const measurementRunsToRefreshNow = [...measurementRunsToRefresh];
        measurementRunsToRefresh.clear();
        invalidateCanonicalQueries();
        for (const runId of measurementRunsToRefreshNow) {
          void queryClient.invalidateQueries({
            queryKey: ["measurements", runId],
            exact: true,
          });
          void queryClient.invalidateQueries({
            queryKey: ["measurements", "live", runId],
            exact: true,
          });
          void queryClient.invalidateQueries({
            queryKey: ["measurement-slice", runId],
          });
          void queryClient.invalidateQueries({
            queryKey: ["measurement-trace", runId],
          });
        }
      }, 100);
    };
    events.addEventListener("open", refreshAfterConnection);
    events.addEventListener("project", refresh);
    return () => {
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
      events.removeEventListener("open", refreshAfterConnection);
      events.removeEventListener("project", refresh);
      events.close();
    };
  }, [healthQuery.isSuccess, queryClient]);

  const daemonReachable = healthQuery.isSuccess;
  const daemonUnavailable = healthQuery.isError;
  const waitingDecisions = useQuery({
    queryKey: ["procedure-decisions"],
    queryFn: ({ signal }) => getWaitingProcedures(signal),
    enabled: daemonReachable && !focused,
    refetchInterval: 1_000,
  });

  const refresh = () => {
    void queryClient.invalidateQueries();
  };
  const selectView = (selected: ProjectView, preparation = false) => {
    setView(selected);
    navigateToView(selected, {
      analysisId: selected === "analyses" ? selectedAnalysisId : undefined,
      runId: selected === "runs" ? selectedRunId : undefined,
      sampleId: selected === "samples" ? selectedSampleId : undefined,
      sampleRevision: selected === "samples" ? selectedSampleRevision : undefined,
      preparation,
    });
    window.scrollTo({ top: 0, left: 0 });
  };
  const selectRun = useCallback((runId: string, options?: NavigationOptions) => {
    setSelectedRunId(runId);
    navigateToView("runs", { runId }, options);
  }, []);
  const selectAnalysis = useCallback((analysisId: string, options?: NavigationOptions) => {
    setSelectedAnalysisId(analysisId);
    navigateToView(
      "analyses",
      { analysisId },
      {
        ...options,
        state: { scopecatComparison: window.history.state?.scopecatComparison, ...options?.state },
      },
    );
  }, []);
  const selectSample = useCallback((sampleId: string, revision?: number) => {
    setSelectedSampleId(sampleId);
    setSelectedSampleRevision(revision);
    navigateToView("samples", { sampleId, sampleRevision: revision });
  }, []);
  const openConfigSourceRun = (runId: string) => {
    setSelectedRunId(runId);
    setView("runs");
    navigateToView("runs", { runId });
    window.scrollTo({ top: 0, left: 0 });
  };
  const openRunSample = (sampleId: string, revision: number) => {
    setSelectedSampleId(sampleId);
    setSelectedSampleRevision(revision);
    setView("samples");
    navigateToView("samples", { sampleId, sampleRevision: revision });
    window.scrollTo({ top: 0, left: 0 });
  };

  return (
    <div className="workspace-shell">
      <WorkspaceNavigation
        view={view}
        onSelect={selectView}
        projectName={healthQuery.data?.projectName}
        focused={focused}
        decisionAttention={
          daemonUnavailable || waitingDecisions.isError
            ? "状态未知"
            : waitingDecisions.data
              ? `${waitingDecisions.data.items.length}${waitingDecisions.data.next_cursor != null ? "+" : ""} 待处理`
              : undefined
        }
        onShowNavigation={() => {
          const url = new URL(window.location.href);
          url.searchParams.delete("focus");
          navigate(url, { replace: true });
        }}
      />
      <div id="workspace-body">
        <header className="workspace-top">
          <span className="workspace-top-title">
            {healthQuery.data?.projectName ?? "Scopecat"} · {projectViewLabel(view)}
          </span>
          <div className="flex items-center gap-2.5">
            <ConnectionState
              reachable={daemonReachable}
              pending={healthQuery.isPending}
              status={healthQuery.data?.status}
            />
            <button
              className={iconButton}
              type="button"
              onClick={refresh}
              aria-label="刷新项目数据"
              aria-busy={activeQueries > 0}
              title={activeQueries > 0 ? "正在刷新项目数据" : "刷新项目数据"}
            >
              <RefreshCw
                size={17}
                className={activeQueries > 0 ? "animate-spin" : undefined}
                aria-hidden="true"
              />
            </button>
          </div>
        </header>

        <main className="workspace-main">
          {daemonUnavailable && (
            <div
              className="mb-[18px] flex items-center gap-[11px] rounded-md border border-[rgb(255_140_136_/_27%)] bg-red-soft px-[15px] py-[13px] text-sm leading-6 text-red"
              role="status"
            >
              <Unplug className="flex-none text-red" size={18} aria-hidden="true" />
              <span>
                <strong className="text-red">连接已中断。</strong>{" "}
                {window.pywebview
                  ? "若 Scopecat 正在重启或添加代码文件夹，请等待完成；否则请在“设置 → 重启应用”中重新连接。"
                  : "请确认 Scopecat 正在运行，再刷新此页面。"}{" "}
                已保存的记录仍保留；重新连接前无法获取实时项目数据。
              </span>
            </div>
          )}

          <LaunchDraftProvider projectId={healthQuery.data?.projectId}>
            <PinnedTask />
            {
              <RetainedView mode={view === "launch" ? "visible" : "hidden"}>
                <Suspense fallback={<p role="status">正在加载实验…</p>}>
                  <LaunchWorkspace
                    handoff={
                      handoff?.projectId === healthQuery.data?.projectId
                        ? handoff?.value
                        : undefined
                    }
                    onHandoffImported={() => setHandoff(undefined)}
                  />
                </Suspense>
              </RetainedView>
            }
            <div hidden={view !== "configuration"}>
              <Suspense
                fallback={
                  <DetailEmpty
                    icon={<LoaderCircle className="animate-spin" />}
                    title="正在加载参数与绑定"
                    detail="正在准备参数与绑定工作区。"
                  />
                }
              >
                <ContextConfigWorkspace
                  workspaceRef={parameterWorkspace}
                  objectPanel={objectPanel}
                  onSelected={() => selectView("launch", true)}
                  daemonUnavailable={daemonUnavailable}
                />
              </Suspense>
            </div>
            {view === "runs" && !healthQuery.isPending && (
              <RunsWorkspace
                focused={focused}
                selectedRunId={selectedRunId}
                onSelectRun={selectRun}
                health={healthQuery.data}
                healthPending={healthQuery.isPending}
                healthReachable={daemonReachable}
                daemonUnavailable={daemonUnavailable}
                onOpenSample={openRunSample}
              />
            )}
          </LaunchDraftProvider>
          {healthQuery.data?.projectId && (
            <RetainedView
              key={healthQuery.data.projectId}
              mode={view === "history" ? "visible" : "hidden"}
            >
              <Suspense fallback={<p role="status">正在加载数据…</p>}>
                <ResearchHistory
                  projectId={healthQuery.data.projectId}
                  daemonUnavailable={daemonUnavailable}
                  onOpenRun={openConfigSourceRun}
                />
              </Suspense>
            </RetainedView>
          )}
          {view === "help" ? (
            <Suspense fallback={<p role="status">正在加载帮助…</p>}>
              <HelpWorkspace health={healthQuery.data} reachable={daemonReachable} />
            </Suspense>
          ) : view === "settings" ? (
            <Suspense fallback={<p role="status">正在加载设置…</p>}>
              <ApplicationSettings health={healthQuery.data} />
            </Suspense>
          ) : view === "samples" ? (
            <Suspense
              fallback={
                <DetailEmpty
                  icon={<LoaderCircle className="animate-spin" />}
                  title="正在加载样品"
                  detail="正在准备样品登记信息。"
                />
              }
            >
              <SamplesWorkspace
                onEditParameters={editObjectParameters}
                onParameterPanel={setObjectPanel}
                daemonUnavailable={daemonUnavailable}
                onOpenRun={openConfigSourceRun}
                onSelectSample={selectSample}
                selectedSampleId={selectedSampleId}
                selectedSampleRevision={selectedSampleRevision}
              />
            </Suspense>
          ) : view === "analyses" ? (
            <Suspense
              fallback={
                <DetailEmpty
                  icon={<LoaderCircle className="animate-spin" />}
                  title="正在加载分析比较"
                  detail="正在准备跨运行分析工作区。"
                />
              }
            >
              <AnalysesWorkspace
                projectId={healthQuery.data?.projectId}
                onHandoff={(request) => {
                  setHandoff({ projectId: healthQuery.data?.projectId, value: request });
                  selectView("launch");
                }}
                daemonUnavailable={daemonUnavailable}
                onOpenRun={openConfigSourceRun}
                onSelectAnalysis={selectAnalysis}
                selectedAnalysisId={selectedAnalysisId}
              />
            </Suspense>
          ) : view === "launch" || view === "history" ? null : view === "decisions" ? (
            <Suspense
              fallback={
                <DetailEmpty
                  icon={<LoaderCircle className="animate-spin" />}
                  title="正在加载人工判断"
                  detail="正在准备实验人工判断队列。"
                />
              }
            >
              <DecisionWorkspace daemonUnavailable={daemonUnavailable} />
            </Suspense>
          ) : view === "reviews" ? (
            <Suspense
              fallback={
                <DetailEmpty
                  icon={<LoaderCircle className="animate-spin" />}
                  title="正在加载检查会话"
                  detail="正在准备编译波形检查工作区。"
                />
              }
            >
              <ReviewWorkspace daemonUnavailable={daemonUnavailable} />
            </Suspense>
          ) : view === "instruments" ? (
            <Suspense
              fallback={
                <DetailEmpty
                  icon={<LoaderCircle className="animate-spin" />}
                  title="正在加载设备与驱动"
                  detail="正在准备设备与驱动工作区。"
                />
              }
            >
              <InstrumentsWorkspace daemonUnavailable={daemonUnavailable} />
            </Suspense>
          ) : null}
        </main>
      </div>
      <DesktopSession />
      <DesktopFiles />
      <DesktopZoom />
      <DesktopFind />
    </div>
  );
}

function ConnectionState({
  reachable,
  pending,
  status,
}: {
  reachable: boolean;
  pending: boolean;
  status?: string;
}) {
  const label = pending
    ? "正在连接"
    : !reachable
      ? "连接已断开"
      : status === "ok"
        ? "已连接"
        : status === "degraded"
          ? "服务受限"
          : status
            ? `状态未知（${status}）`
            : "状态未知";
  return (
    <span
      className={classes(
        "inline-flex min-h-7 items-center gap-2 rounded-sm border-0 bg-transparent px-2 text-xs font-bold max-[680px]:min-h-[30px] max-[680px]:px-[9px] max-[680px]:text-xs max-[460px]:max-w-[120px] max-[460px]:overflow-hidden max-[460px]:text-ellipsis max-[460px]:whitespace-nowrap",
        pending || reachable ? "text-text-soft" : "text-red",
      )}
      role="status"
      title={label}
    >
      <span
        className={classes(
          "size-[7px] flex-none rounded-full",
          !pending && !reachable
            ? "bg-red shadow-[0_0_0_3px_rgb(116_131_146_/_12%)]"
            : !pending && status === "ok"
              ? "bg-accent shadow-[0_0_0_3px_var(--color-accent-soft)]"
              : "bg-text-dim",
        )}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

function DetailEmpty({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <div
      role="status"
      className="grid min-h-[590px] place-content-center justify-items-center text-center max-[880px]:min-h-[500px]"
    >
      <span
        className="mb-4 grid size-[55px] place-items-center rounded-[14px] border border-line bg-panel-soft text-text-soft [&>svg]:w-[23px]"
        aria-hidden="true"
      >
        {icon}
      </span>
      <h2 className="m-0 text-base">{title}</h2>
      <p className="mt-2 mb-0 max-w-[410px] text-xs leading-[1.55] text-text-dim">{detail}</p>
    </div>
  );
}

function selectedRunFromUrl(): string | undefined {
  return new URL(window.location.href).searchParams.get("run") || undefined;
}

function selectedAnalysisFromUrl(): string | undefined {
  return new URL(window.location.href).searchParams.get("analysis") || undefined;
}

function selectedSampleFromUrl(): string | undefined {
  return new URL(window.location.href).searchParams.get("sample") || undefined;
}

function selectedSampleRevisionFromUrl(): number | undefined {
  const value = new URL(window.location.href).searchParams.get("sample-revision");
  if (value === null || !/^\d+$/.test(value)) return undefined;
  const revision = Number(value);
  return revision >= 1 ? revision : undefined;
}

function projectViewFromLocation(): ProjectView {
  if (window.location.hash === "#help") return "help";
  if (window.location.hash === "#settings") return "settings";
  if (window.location.hash === "#launch") return "launch";
  if (window.location.hash === "#configuration") return "configuration";
  if (window.location.hash === "#instruments") return "instruments";
  if (window.location.hash === "#analyses") return "analyses";
  if (window.location.hash === "#history") return "history";
  if (window.location.hash === "#samples") return "samples";
  if (window.location.hash === "#decisions") return "decisions";
  if (window.location.hash.startsWith("#reviews")) return "reviews";
  return "runs";
}

function navigateToView(
  view: ProjectView,
  selection: {
    analysisId?: string;
    runId?: string;
    sampleId?: string;
    sampleRevision?: number;
    preparation?: boolean;
  } = {},
  options?: NavigationOptions,
): void {
  const location = new URL(window.location.href);
  if (location.searchParams.get("run") !== selection.runId)
    location.searchParams.delete("run-analysis");
  if (location.searchParams.get("sample") !== selection.sampleId)
    location.searchParams.delete("sample-analysis");
  if (selection.runId) {
    location.searchParams.set("run", selection.runId);
  } else {
    location.searchParams.delete("run");
  }
  if (selection.analysisId) {
    location.searchParams.delete("compare");
    location.searchParams.delete("comparison-analysis");
    location.searchParams.delete("workspace");
    location.searchParams.set("analysis", selection.analysisId);
  } else {
    location.searchParams.delete("analysis");
  }
  if (selection.sampleId) {
    location.searchParams.set("sample", selection.sampleId);
  } else {
    location.searchParams.delete("sample");
  }
  if (selection.sampleRevision !== undefined) {
    location.searchParams.set("sample-revision", String(selection.sampleRevision));
  } else {
    location.searchParams.delete("sample-revision");
  }
  if (selection.preparation) {
    location.searchParams.delete("workbench");
    location.searchParams.set("preparation", "1");
  }
  location.hash = view === "runs" ? "" : view;
  navigate(location, options);
}

function measurementEventRunId(event: Event): string | undefined {
  if (!(event instanceof MessageEvent) || typeof event.data !== "string") {
    return undefined;
  }
  try {
    const payload: unknown = JSON.parse(event.data);
    if (typeof payload !== "object" || payload === null) return undefined;
    const source = payload as Record<string, unknown>;
    if (
      source.kind !== "measurement_dataset_initialized" &&
      source.kind !== "measurements_appended" &&
      source.kind !== "measurements_sealed"
    ) {
      return undefined;
    }
    return typeof source.run_id === "string" ? source.run_id : undefined;
  } catch {
    return undefined;
  }
}

function ContextConfigWorkspace({
  workspaceRef,
  objectPanel,
  daemonUnavailable,
  onSelected,
}: {
  daemonUnavailable: boolean;
  onSelected: () => void;
  workspaceRef: React.Ref<ParameterWorkspaceHandle>;
  objectPanel: ObjectParameterPanel | null;
}) {
  const { selectConfiguration } = useLaunchDraft();
  return (
    <ConfigWorkspace
      ref={workspaceRef}
      objectPanel={objectPanel}
      daemonUnavailable={daemonUnavailable}
      onSelectConfiguration={(ref, workingInput, subject) => {
        selectConfiguration(ref, workingInput, subject);
        onSelected();
      }}
    />
  );
}
