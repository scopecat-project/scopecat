import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Atom,
  Activity,
  Boxes,
  Cable,
  GitCompareArrows,
  LayoutDashboard,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  Settings2,
  Unplug,
} from "lucide-react";
import { parameterProposalKeys } from "./data/parameter-proposals/query-keys";
import { getEvents, getHealth } from "./data/project-api";
import { LaunchDraftProvider, useLaunchDraft } from "./features/launch/LaunchDraft";
import { RunsWorkspace } from "./features/runs/RunsWorkspace";
import { titleCase } from "./lib/presentation";
import { classes, iconButton } from "./ui/styles";

type ProjectView =
  | "launch"
  | "runs"
  | "samples"
  | "analyses"
  | "decisions"
  | "reviews"
  | "instruments"
  | "configuration";

const LaunchWorkspace = lazy(async () => {
  const module = await import("./features/launch/LaunchWorkspace");
  return { default: module.LaunchWorkspace };
});

const AnalysesWorkspace = lazy(async () => {
  const module = await import("./features/analyses/AnalysesWorkspace");
  return { default: module.AnalysesWorkspace };
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
  const [view, setView] = useState<ProjectView>(projectViewFromLocation);
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
      setView(projectViewFromLocation());
      setSelectedRunId(selectedRunFromUrl());
      setSelectedAnalysisId(selectedAnalysisFromUrl());
      setSelectedSampleId(selectedSampleFromUrl());
      setSelectedSampleRevision(selectedSampleRevisionFromUrl());
    };
    window.addEventListener("hashchange", restoreHashRoute);
    return () => window.removeEventListener("hashchange", restoreHashRoute);
  }, []);

  useEffect(() => {
    if (!healthQuery.isSuccess) return;
    const events = new EventSource(`/api/v1/events/stream?after=${eventCursor.current}`);
    let refreshTimer: number | undefined;
    const measurementRunsToReset = new Set<string>();
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
        measurementRunsToReset.add(measurementRunId);
      }
      if (refreshTimer !== undefined) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined;
        const resetMeasurementRuns = [...measurementRunsToReset];
        measurementRunsToReset.clear();
        invalidateCanonicalQueries();
        for (const runId of resetMeasurementRuns) {
          void queryClient.resetQueries({
            queryKey: ["measurements", runId],
            exact: true,
          });
          void queryClient.resetQueries({
            queryKey: ["measurements", "live", runId],
            exact: true,
          });
          void queryClient.resetQueries({
            queryKey: ["measurement-slice", runId],
          });
          void queryClient.resetQueries({
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
  const lastUpdated = Math.max(healthQuery.dataUpdatedAt, eventsQuery.dataUpdatedAt);

  const refresh = () => {
    void queryClient.invalidateQueries();
  };
  const selectView = (selected: ProjectView) => {
    setView(selected);
    replaceNavigation(selected, {
      analysisId: selected === "analyses" ? selectedAnalysisId : undefined,
      runId: selected === "runs" ? selectedRunId : undefined,
      sampleId: selected === "samples" ? selectedSampleId : undefined,
      sampleRevision: selected === "samples" ? selectedSampleRevision : undefined,
    });
    window.scrollTo({ top: 0, left: 0 });
  };
  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId);
    replaceNavigation("runs", { runId });
  }, []);
  const selectAnalysis = useCallback((analysisId: string) => {
    setSelectedAnalysisId(analysisId);
    replaceNavigation("analyses", { analysisId });
  }, []);
  const selectSample = useCallback((sampleId: string, revision?: number) => {
    setSelectedSampleId(sampleId);
    setSelectedSampleRevision(revision);
    replaceNavigation("samples", { sampleId, sampleRevision: revision });
  }, []);
  const openConfigSourceRun = (runId: string) => {
    setSelectedRunId(runId);
    setView("runs");
    replaceNavigation("runs", { runId });
    window.scrollTo({ top: 0, left: 0 });
  };
  const openRunSample = (sampleId: string, revision: number) => {
    setSelectedSampleId(sampleId);
    setSelectedSampleRevision(revision);
    setView("samples");
    replaceNavigation("samples", { sampleId, sampleRevision: revision });
    window.scrollTo({ top: 0, left: 0 });
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 flex h-12 items-center justify-between border-b border-line bg-bg-raised px-5 max-[680px]:h-auto max-[680px]:min-h-[60px] max-[680px]:flex-wrap max-[680px]:gap-2 max-[680px]:px-[15px] max-[680px]:py-[9px]">
        <a
          className="inline-flex items-center gap-2 text-inherit no-underline"
          href="/"
          aria-label="Scopecat project console"
        >
          <span
            className="grid size-7 place-items-center rounded-sm border border-line-strong bg-panel text-accent"
            aria-hidden="true"
          >
            <Atom size={20} strokeWidth={1.8} />
          </span>
          <span className="grid gap-px">
            <strong className="text-[0.82rem] tracking-[0.02em]">Scopecat</strong>
            <small className="hidden">Project console</small>
          </span>
        </a>
        <nav
          className="absolute left-1/2 flex -translate-x-1/2 gap-4 max-[880px]:static max-[880px]:ml-auto max-[880px]:translate-x-0 max-[680px]:order-3 max-[680px]:w-full max-[680px]:overflow-x-auto"
          aria-label="Project sections"
        >
          <button
            type="button"
            className={navigationClass(view === "launch")}
            aria-current={view === "launch" ? "page" : undefined}
            onClick={() => selectView("launch")}
          >
            <Activity size={15} aria-hidden="true" />
            Experiments
          </button>
          <button
            type="button"
            className={navigationClass(view === "samples")}
            aria-current={view === "samples" ? "page" : undefined}
            onClick={() => selectView("samples")}
          >
            <Boxes size={15} aria-hidden="true" />
            Samples
          </button>
          <button
            type="button"
            className={navigationClass(view === "runs")}
            aria-current={view === "runs" ? "page" : undefined}
            onClick={() => selectView("runs")}
          >
            <LayoutDashboard size={15} aria-hidden="true" />
            Runs
          </button>
          <button
            type="button"
            className={navigationClass(view === "analyses")}
            aria-current={view === "analyses" ? "page" : undefined}
            onClick={() => selectView("analyses")}
          >
            <GitCompareArrows size={15} aria-hidden="true" />
            Analyses
          </button>
          <button
            type="button"
            className={navigationClass(view === "decisions")}
            aria-current={view === "decisions" ? "page" : undefined}
            onClick={() => selectView("decisions")}
          >
            <MessageSquareText size={15} aria-hidden="true" />
            Decisions
          </button>
          <button
            type="button"
            className={navigationClass(view === "reviews")}
            aria-current={view === "reviews" ? "page" : undefined}
            onClick={() => selectView("reviews")}
          >
            <Activity size={15} aria-hidden="true" />
            Reviews
          </button>
          <button
            type="button"
            className={navigationClass(view === "instruments")}
            aria-current={view === "instruments" ? "page" : undefined}
            onClick={() => selectView("instruments")}
          >
            <Cable size={15} aria-hidden="true" />
            Instruments
          </button>
          <button
            type="button"
            className={navigationClass(view === "configuration")}
            aria-current={view === "configuration" ? "page" : undefined}
            onClick={() => selectView("configuration")}
          >
            <Settings2 size={15} aria-hidden="true" />
            Configuration
          </button>
        </nav>
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
            aria-label="Refresh project data"
            title="Refresh project data"
          >
            <RefreshCw
              size={17}
              className={activeQueries > 0 ? "animate-spin" : undefined}
              aria-hidden="true"
            />
          </button>
        </div>
      </header>

      <main className="mx-auto w-[min(1680px,calc(100%-32px))] py-[14px] pb-8 max-[1100px]:w-[min(100%-28px,1200px)] max-[680px]:w-[calc(100%-20px)] max-[680px]:py-3 max-[680px]:pb-8">
        <section
          className="mb-2.5 flex min-h-[34px] items-center justify-between gap-5 px-0.5 max-[680px]:items-start"
          aria-labelledby="workspace-title"
        >
          <div className="flex min-w-0 items-baseline gap-3 max-[680px]:grid max-[680px]:gap-[3px]">
            <h1
              className="m-0 flex-none text-base font-[650] tracking-[-0.015em]"
              id="workspace-title"
            >
              {healthQuery.data?.projectName ?? "Scopecat project"}
            </h1>
            {healthQuery.data?.projectRoot && (
              <code className="max-w-[min(60vw,900px)] overflow-hidden text-[0.64rem] text-ellipsis whitespace-nowrap text-text-dim max-[680px]:max-w-[65vw]">
                {healthQuery.data.projectRoot}
              </code>
            )}
          </div>
          <div
            className="inline-flex flex-none items-center gap-[7px] text-[0.64rem] font-semibold text-text-dim"
            aria-live="polite"
          >
            {lastUpdated > 0
              ? `Updated ${formatClock(new Date(lastUpdated).toISOString())}`
              : "Waiting for daemon"}
          </div>
        </section>

        {daemonUnavailable && (
          <div
            className="mb-[18px] flex items-center gap-[11px] rounded-md border border-[rgb(255_140_136_/_27%)] bg-red-soft px-[15px] py-[13px] text-[0.82rem] leading-6 text-[#efc3c0]"
            role="status"
          >
            <Unplug className="flex-none text-red" size={18} aria-hidden="true" />
            <span>
              <strong className="text-[#ffe6e4]">Daemon unavailable.</strong> Start the local
              Scopecat daemon, then refresh this page. No cached project data is shown.
            </span>
          </div>
        )}

        <LaunchDraftProvider projectId={healthQuery.data?.projectId}>
          {view === "launch" && (
            <Suspense fallback={<p>Loading experiments…</p>}>
              <LaunchWorkspace />
            </Suspense>
          )}
          {view === "configuration" && (
            <Suspense
              fallback={
                <DetailEmpty
                  icon={<LoaderCircle className="animate-spin" />}
                  title="Loading configuration"
                  detail="The configuration workspace is being prepared."
                />
              }
            >
              <ContextConfigWorkspace
                onSelected={() => selectView("launch")}
                daemonUnavailable={daemonUnavailable}
                onOpenRun={openConfigSourceRun}
              />
            </Suspense>
          )}
        </LaunchDraftProvider>
        {view === "runs" ? (
          <RunsWorkspace
            selectedRunId={selectedRunId}
            onSelectRun={selectRun}
            health={healthQuery.data}
            healthPending={healthQuery.isPending}
            healthReachable={daemonReachable}
            daemonUnavailable={daemonUnavailable}
            onOpenSample={openRunSample}
          />
        ) : view === "samples" ? (
          <Suspense
            fallback={
              <DetailEmpty
                icon={<LoaderCircle className="animate-spin" />}
                title="Loading samples"
                detail="The physical sample registry is being prepared."
              />
            }
          >
            <SamplesWorkspace
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
                title="Loading analyses"
                detail="The cross-run analysis workspace is being prepared."
              />
            }
          >
            <AnalysesWorkspace
              daemonUnavailable={daemonUnavailable}
              onOpenRun={openConfigSourceRun}
              onSelectAnalysis={selectAnalysis}
              selectedAnalysisId={selectedAnalysisId}
            />
          </Suspense>
        ) : view === "launch" ? null : view === "decisions" ? (
          <Suspense
            fallback={
              <DetailEmpty
                icon={<LoaderCircle className="animate-spin" />}
                title="Loading decisions"
                detail="The experiment interpretation queue is being prepared."
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
                title="Loading reviews"
                detail="The compiled waveform workspace is being prepared."
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
                title="Loading instruments"
                detail="The instrument workspace is being prepared."
              />
            }
          >
            <InstrumentsWorkspace daemonUnavailable={daemonUnavailable} />
          </Suspense>
        ) : null}
      </main>
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
    ? "Connecting"
    : reachable
      ? titleCase(status ?? "connected")
      : "Disconnected";
  return (
    <span
      className={classes(
        "inline-flex min-h-7 items-center gap-2 rounded-sm border-0 bg-transparent px-2 text-[0.69rem] font-bold max-[680px]:min-h-[30px] max-[680px]:px-[9px] max-[680px]:text-[0.68rem] max-[460px]:max-w-[120px] max-[460px]:overflow-hidden max-[460px]:text-ellipsis max-[460px]:whitespace-nowrap",
        reachable ? "text-text-soft" : "text-[#eab5b2]",
      )}
      role="status"
    >
      <span
        className={classes(
          "size-[7px] flex-none rounded-full",
          reachable
            ? "bg-accent shadow-[0_0_0_3px_var(--color-accent-soft)]"
            : "bg-red shadow-[0_0_0_3px_rgb(116_131_146_/_12%)]",
        )}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

function navigationClass(active: boolean): string {
  return classes(
    "inline-flex min-h-[47px] cursor-pointer items-center gap-[7px] border-0 border-b-2 border-transparent bg-transparent px-px text-[0.69rem] font-[750] text-text-dim hover:text-text-soft max-[880px]:px-2 max-[680px]:flex-1",
    active && "border-b-accent text-text",
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
  if (window.location.hash === "#launch") return "launch";
  if (window.location.hash === "#configuration") return "configuration";
  if (window.location.hash === "#instruments") return "instruments";
  if (window.location.hash === "#analyses") return "analyses";
  if (window.location.hash === "#samples") return "samples";
  if (window.location.hash === "#decisions") return "decisions";
  if (window.location.hash.startsWith("#reviews")) return "reviews";
  return "runs";
}

function replaceNavigation(
  view: ProjectView,
  selection: {
    analysisId?: string;
    runId?: string;
    sampleId?: string;
    sampleRevision?: number;
  } = {},
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
  location.hash = view === "runs" ? "" : view;
  window.history.replaceState(null, "", `${location.pathname}${location.search}${location.hash}`);
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

function formatClock(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function ContextConfigWorkspace({
  daemonUnavailable,
  onOpenRun,
  onSelected,
}: {
  daemonUnavailable: boolean;
  onOpenRun: (id: string) => void;
  onSelected: () => void;
}) {
  const { selectContext } = useLaunchDraft();
  return (
    <ConfigWorkspace
      daemonUnavailable={daemonUnavailable}
      onOpenRun={onOpenRun}
      onSelectContext={(resolved) => {
        selectContext(resolved);
        onSelected();
      }}
    />
  );
}
