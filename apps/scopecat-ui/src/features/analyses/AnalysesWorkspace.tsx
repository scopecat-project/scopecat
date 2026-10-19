import * as Controls from "../../ui/Controls";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Atom, ChevronDown, CircleOff, LoaderCircle } from "lucide-react";
import { errorMessage, formatDateTime, formatRelative } from "../../lib/presentation";
import { navigate, useLocationUrl, type NavigationOptions } from "../../lib/navigation";
import type { ProjectAnalysis, ProjectAnalysisSummary } from "../../types";
import { classes, countBadge, secondaryButton } from "../../ui/styles";
import { AnalysisPublicationView } from "./AnalysisPublicationView";
import { ClearData } from "../history/DataCleanup";
import {
  getProjectAnalysis,
  getProjectAnalysisArtifactDownload,
  getProjectAnalysisSummaries,
  getOlderProjectAnalysisSummaries,
} from "./analysis-api";

import { RunComparison, type ComparisonHandoff } from "./RunComparison";

export function AnalysesWorkspace(props: {
  projectId: string | undefined;
  daemonUnavailable: boolean;
  onOpenRun: (runId: string) => void;
  onSelectAnalysis: (analysisId: string, options?: NavigationOptions) => void;
  selectedAnalysisId?: string;
  onHandoff: (handoff: ComparisonHandoff) => void;
}) {
  const url = useLocationUrl();
  // An exact project publication also disambiguates older links carrying both parameters.
  const comparison = url.searchParams.has("compare") && !url.searchParams.has("analysis");
  const entry = `${props.projectId}:${window.history.state?.scopecatPosition ?? 0}`;
  function selectWorkflow(compare: boolean) {
    if (compare === comparison) return;
    const target = new URL(url);
    const saved = window.history.state?.scopecatAnalysisSelection;
    const analysisId =
      props.selectedAnalysisId ?? (saved?.projectId === props.projectId ? saved.id : undefined);
    const draft = window.history.state?.scopecatComparison;
    target.hash = "analyses";
    target.searchParams.delete("analysis");
    target.searchParams.delete("compare");
    target.searchParams.delete("comparison-analysis");
    target.searchParams.delete("workspace");
    if (compare) {
      target.searchParams.set(
        "compare",
        draft?.projectId === props.projectId ? (draft.draft?.primary ?? "") : "",
      );
      if (draft?.projectId === props.projectId && draft.draft?.selected)
        target.searchParams.set("comparison-analysis", draft.draft.selected);
    } else if (analysisId) target.searchParams.set("analysis", analysisId);
    // Switching workflows carries only this window's draft and last project selection.
    // Each history entry still owns its own snapshot and return position.
    navigate(target, {
      state: {
        scopecatComparison: draft,
        scopecatAnalysisSelection: { projectId: props.projectId, id: analysisId },
      },
    });
  }
  return (
    <div className="space-y-4">
      <nav className="flex gap-4" aria-label="Analysis workflows">
        <Controls.Button
          aria-current={comparison ? "page" : undefined}
          onClick={() => selectWorkflow(true)}
        >
          Compare retained runs
        </Controls.Button>
        <Controls.Button
          aria-current={!comparison ? "page" : undefined}
          onClick={() => selectWorkflow(false)}
        >
          Project analysis history
        </Controls.Button>
      </nav>
      {comparison && !props.daemonUnavailable ? (
        <RunComparison
          key={entry}
          projectId={props.projectId}
          onOpenRun={props.onOpenRun}
          onHandoff={props.onHandoff}
        />
      ) : (
        <ProjectAnalyses key={entry} {...props} />
      )}
    </div>
  );
}

function ProjectAnalyses({
  projectId,
  daemonUnavailable,
  onOpenRun,
  onSelectAnalysis,
  selectedAnalysisId,
}: {
  projectId: string | undefined;
  daemonUnavailable: boolean;
  onOpenRun: (runId: string) => void;
  onSelectAnalysis: (analysisId: string, options?: NavigationOptions) => void;
  selectedAnalysisId?: string;
}) {
  const analyses = useInfiniteQuery({
    queryKey: ["analyses", "project"],
    queryFn: ({ pageParam, signal }) =>
      pageParam === undefined
        ? getProjectAnalysisSummaries(signal)
        : getOlderProjectAnalysisSummaries(pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.nextCursor,
    enabled: !daemonUnavailable,
  });
  const summaries = useMemo(() => {
    const items = new Map<string, ProjectAnalysisSummary>();
    for (const page of analyses.data?.pages ?? []) {
      for (const analysis of page.items) items.set(analysis.id, analysis);
    }
    return [...items.values()];
  }, [analyses.data]);
  const selectedId = selectedAnalysisId ?? summaries[0]?.id;
  const selectedAnalysis = useQuery({
    queryKey: ["analyses", "project", selectedId, "detail"],
    queryFn: ({ signal }) => getProjectAnalysis(selectedId!, signal),
    enabled: !daemonUnavailable && selectedId !== undefined,
  });
  const page = useAnalysisReturnPosition(
    projectId,
    selectedId,
    !daemonUnavailable && !analyses.isPending && selectedAnalysis.isSuccess,
  );

  useEffect(() => {
    if (selectedId && selectedAnalysisId === undefined) {
      onSelectAnalysis(selectedId, { replace: true });
    }
  }, [onSelectAnalysis, selectedAnalysisId, selectedId]);

  if (daemonUnavailable) {
    return (
      <WorkspaceMessage
        icon={<CircleOff />}
        title="Connect to the local daemon"
        detail="Project analysis publications are read directly from the daemon-owned project store."
      />
    );
  }
  if (analyses.isPending) {
    return (
      <WorkspaceMessage
        icon={<LoaderCircle className="animate-spin" />}
        title="Loading project analyses"
        detail="Reading immutable cross-run publications and their provenance."
      />
    );
  }
  if (analyses.isError && analyses.data === undefined) {
    return (
      <WorkspaceMessage
        icon={<CircleOff />}
        title="Project analyses unavailable"
        detail={errorMessage(analyses.error)}
      />
    );
  }
  if (!selectedId) {
    return (
      <WorkspaceMessage
        icon={<Atom />}
        title="No project analyses saved"
        detail="Publish a cross-run analysis from Python with lab.analyze(step)."
      />
    );
  }

  return (
    <section
      ref={page}
      className="grid min-h-[680px] grid-cols-[285px_minmax(0,1fr)] overflow-hidden rounded-lg border border-line bg-panel max-[900px]:grid-cols-1"
      aria-labelledby="analyses-heading"
    >
      <aside className="border-r border-line bg-panel-soft p-2.5 max-[900px]:border-r-0 max-[900px]:border-b">
        <div className="flex items-center justify-between px-2 py-2">
          <h2
            className="m-0 text-xs tracking-[0.08em] text-text-dim uppercase"
            id="analyses-heading"
          >
            Project analyses
          </h2>
          <span className={countBadge}>
            {summaries.length}
            {analyses.hasNextPage ? "+" : ""}
          </span>
        </div>
        <div className="grid gap-1.5 max-[900px]:grid-cols-[repeat(auto-fit,minmax(220px,1fr))]">
          {summaries.map((analysis) => (
            <button
              className={classes(
                "grid cursor-pointer gap-1 rounded-md border border-transparent bg-transparent px-2.5 py-2.5 text-left text-text-soft hover:bg-panel",
                analysis.id === selectedId && "border-line-strong bg-panel",
              )}
              key={analysis.id}
              onClick={() => onSelectAnalysis(analysis.id)}
              type="button"
              title={`Inspect analysis ${analysis.id}`}
            >
              <span className="flex items-start justify-between gap-2 text-xs font-bold">
                <span className="line-clamp-2">{analysis.title}</span>
                <span className="flex-none rounded border border-line px-1.5 py-0.5 text-xs text-text-dim">
                  r{analysis.revision}
                </span>
              </span>
              <span className="truncate font-mono text-xs text-text-dim">
                {analysis.key ?? analysis.id}
              </span>
              <span className="text-xs text-text-dim">
                <time dateTime={analysis.publishedAt} title={formatDateTime(analysis.publishedAt)}>
                  {formatRelative(analysis.publishedAt)}
                </time>{" "}
                · {analysis.inputCount} input{analysis.inputCount === 1 ? "" : "s"} ·{" "}
                {analysis.outputCount} output{analysis.outputCount === 1 ? "" : "s"}
              </span>
            </button>
          ))}
          {analyses.isFetchNextPageError ? (
            <p className="mx-2 my-1 text-xs leading-[1.4] text-red" role="status">
              {errorMessage(analyses.error)}
            </p>
          ) : null}
          {analyses.hasNextPage ? (
            <button
              className={classes(secondaryButton, "w-full")}
              disabled={analyses.isFetchingNextPage}
              onClick={() => void analyses.fetchNextPage()}
              type="button"
            >
              {analyses.isFetchingNextPage ? (
                <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
              ) : (
                <ChevronDown size={14} aria-hidden="true" />
              )}
              {analyses.isFetchingNextPage ? "Loading older analyses…" : "Load older analyses"}
            </button>
          ) : null}
        </div>
      </aside>

      <div className="min-w-0 p-4 max-[680px]:p-2.5">
        {selectedAnalysis.isPending ? (
          <WorkspaceMessage
            icon={<LoaderCircle className="animate-spin" />}
            title="Loading analysis detail"
            detail={`Reading ${selectedId} and its exact output provenance.`}
          />
        ) : selectedAnalysis.isError ? (
          <WorkspaceMessage
            icon={<CircleOff />}
            title="Analysis detail unavailable"
            detail={errorMessage(selectedAnalysis.error)}
          />
        ) : selectedAnalysis.data ? (
          <AnalysisDetail analysis={selectedAnalysis.data} onOpenRun={onOpenRun} />
        ) : null}
      </div>
    </section>
  );
}

function useAnalysisReturnPosition(
  projectId: string | undefined,
  analysisId: string | undefined,
  ready: boolean,
) {
  const page = useRef<HTMLElement>(null);
  useEffect(() => {
    const element = page.current;
    if (!ready || !element) return;
    const position = window.history.state?.scopecatPosition ?? 0;
    const saved = window.history.state?.scopecatAnalysisPosition;
    let scrollTimer: number | undefined;
    const save = () => {
      // A navigation may finish before the outgoing component is unmounted.
      if ((window.history.state?.scopecatPosition ?? 0) !== position) return;
      window.history.replaceState(
        {
          ...window.history.state,
          scopecatAnalysisPosition: {
            projectId,
            analysisId,
            scroll: window.scrollY,
            expanded: [...element.querySelectorAll("details")].flatMap((detail, index) =>
              detail.open ? [index] : [],
            ),
          },
        },
        "",
        window.location.href,
      );
    };
    const rememberScroll = () => {
      window.clearTimeout(scrollTimer);
      scrollTimer = window.setTimeout(save, 200);
    };
    const frame = requestAnimationFrame(() => {
      if (saved?.projectId === projectId && saved.analysisId === analysisId) {
        element.querySelectorAll("details").forEach((detail, index) => {
          detail.open = Array.isArray(saved.expanded) && saved.expanded.includes(index);
        });
        if (typeof saved.scroll === "number") window.scrollTo(0, saved.scroll);
      }
      window.addEventListener("scroll", rememberScroll);
      // Save before a link/button changes the history entry, including the top navigation.
      window.addEventListener("click", save, true);
      window.addEventListener("pagehide", save);
      element.addEventListener("toggle", save, true);
    });
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(scrollTimer);
      window.removeEventListener("scroll", rememberScroll);
      window.removeEventListener("click", save, true);
      window.removeEventListener("pagehide", save);
      element.removeEventListener("toggle", save, true);
    };
  }, [projectId, analysisId, ready]);
  return page;
}

function AnalysisDetail({
  analysis: selected,
  onOpenRun,
}: {
  analysis: ProjectAnalysis;
  onOpenRun: (runId: string) => void;
}) {
  return (
    <>
      <header className="mb-3 flex flex-wrap items-start justify-between gap-3 border-b border-line pb-3">
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-2 text-xs font-bold tracking-[0.07em] text-text-dim uppercase">
            <span>Project analysis</span>
            <span>Revision {selected.revision}</span>
            {selected.stepId ? <span>{selected.stepId}</span> : null}
          </div>
          <h3 className="m-0 text-[1.05rem] tracking-[-0.02em]">{selected.title}</h3>
          <code className="text-xs text-text-dim">{selected.id}</code>
        </div>
        <span className={countBadge}>{selected.outputs.length} outputs</span>
      </header>

      <AnalysisPublicationView
        analysis={selected}
        copyTarget={{ kind: "project" }}
        getArtifactDownload={(selector) =>
          getProjectAnalysisArtifactDownload(selected.id, selector)
        }
        onOpenRun={onOpenRun}
      />
      <details className="mt-3 rounded-md border border-line p-3">
        <summary className="cursor-pointer text-sm font-semibold">
          Publication details and data management
        </summary>
        <dl className="mb-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 rounded-md border border-line bg-panel-soft p-3 text-xs">
          <dt className="font-bold text-text-dim">Key</dt>
          <dd className="m-0 font-mono text-text-soft">{selected.key ?? "—"}</dd>
          <dt className="font-bold text-text-dim">Publication</dt>
          <dd className="m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap font-mono text-text-soft">
            <span title={selected.publicationHash}>{selected.publicationHash}</span>
          </dd>
          <dt className="font-bold text-text-dim">Published</dt>
          <dd className="m-0 text-text-soft">
            <time dateTime={selected.publishedAt}>{formatDateTime(selected.publishedAt)}</time>
          </dd>
        </dl>
        <ClearData key={selected.id} analyses={[selected.id]} />
      </details>
    </>
  );
}

function WorkspaceMessage({
  icon,
  title,
  detail,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <div className="grid min-h-[590px] place-content-center justify-items-center text-center">
      <span
        className="mb-4 grid size-[55px] place-items-center rounded-[14px] border border-line bg-panel-soft text-text-soft [&>svg]:w-[23px]"
        aria-hidden="true"
      >
        {icon}
      </span>
      <h2 className="m-0 text-base">{title}</h2>
      <p className="mt-2 mb-0 max-w-[430px] text-xs leading-[1.55] text-text-dim">{detail}</p>
    </div>
  );
}
