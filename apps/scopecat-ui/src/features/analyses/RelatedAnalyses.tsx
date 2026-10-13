import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import type { AnalysisPublication } from "../../types";
import { errorMessage } from "../../lib/presentation";
import { navigateLink, useLocationUrl } from "../../lib/navigation";
import { secondaryButton } from "../../ui/styles";
import { useRunCollectionName } from "../runs/use-run-collection-name";
import { getRun, getRunAnalysis, getRunArtifactDownload } from "../runs/run-api";
import { RepeatRunAnalysis } from "../runs/RepeatRunAnalysis";
import {
  getSample,
  getSampleAnalysis,
  getSampleAnalysisArtifactDownload,
} from "../samples/sample-api";
import { getProjectAnalysis, getProjectAnalysisArtifactDownload } from "./analysis-api";
import { AnalysisPublicationView } from "./AnalysisPublicationView";

type Summary = components["schemas"]["AnalysisPublicationSummary"];
interface RelatedContext {
  projectId: string;
  collectionNames: Map<string, string>;
  onOpenRun?: (runId: string) => void;
  restorePosition?: () => void;
}

export function RelatedAnalyses(props: RelatedContext & { runId: string }) {
  useLocationUrl();
  return (
    <RunResults
      key={`${props.projectId}:${props.runId}:${window.history.state?.scopecatPosition ?? 0}`}
      {...props}
    />
  );
}

function RunResults({
  runId,
  projectId,
  collectionNames,
  onOpenRun,
}: RelatedContext & { runId: string }) {
  const client = useQueryClient();
  const linked = new URLSearchParams(location.search).get("run-analysis");
  const [expanded, setExpanded] = useState<string[]>(() => {
    const saved = window.history.state?.runResults;
    return saved?.projectId === projectId && saved.runId === runId && Array.isArray(saved.expanded)
      ? saved.expanded.filter((id: unknown) => typeof id === "string")
      : [];
  });
  const remember = (ids: string[]) => {
    setExpanded(ids);
    window.history.replaceState(
      { ...window.history.state, runResults: { projectId, runId, expanded: ids } },
      "",
      location.href,
    );
  };
  const restored = useRef(false);
  const restorePosition = () => {
    const saved = window.history.state?.runResults;
    // A cold detail above the saved position must finish before scroll restoration.
    const settled = expanded.every((id) => {
      const state = client.getQueryState(["analysis", "publication", projectId, id]);
      return state?.status === "success" || state?.status === "error";
    });
    if (
      settled &&
      !restored.current &&
      saved?.projectId === projectId &&
      saved.runId === runId &&
      typeof saved.scroll === "number"
    ) {
      restored.current = true;
      requestAnimationFrame(() => window.scrollTo(0, saved.scroll));
    }
  };
  const openInput = (id: string) => {
    window.history.replaceState(
      {
        ...window.history.state,
        runResults: { projectId, runId, expanded, scroll: window.scrollY },
      },
      "",
      location.href,
    );
    onOpenRun?.(id);
  };
  const query = useInfiniteQuery({
    queryKey: ["analyses", "related", projectId, runId, "including-owned"],
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/analysis-publications", {
          params: {
            query: { input_run_id: runId, include_owned: true, limit: 100, before: pageParam },
          },
          signal,
        }),
      ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const linkedDetail = useQuery({
    queryKey: ["analysis", "linked-run", projectId, runId, linked],
    queryFn: ({ signal }) => getRunAnalysis(runId, linked!, signal),
    enabled: Boolean(linked),
  });
  const items = [
    ...new Map(
      query.data?.pages.flatMap((page) => page.items ?? []).map((item) => [item.record.id, item]),
    ).values(),
  ];
  const linkedId = linkedDetail.data?.id ?? linked;
  return (
    <section className="grid min-w-0 gap-2 rounded-lg border border-line p-4" aria-label="Analyses">
      <h3 className="m-0 text-sm font-semibold">Analyses</h3>
      <p className="m-0 text-xs text-text-dim">
        Results saved on this run or directly using its measurements or configuration, wherever
        saved. Each publication appears once.
      </p>
      {linked && (
        <section className="min-w-0 rounded border border-line p-3" aria-label="Linked analysis">
          {linkedDetail.isPending ? (
            <p>Reading linked analysis…</p>
          ) : linkedDetail.isError ? (
            <p role="alert">Linked analysis unavailable: {errorMessage(linkedDetail.error)}</p>
          ) : (
            <>
              <h4>
                {linkedDetail.data.title} · revision {linkedDetail.data.revision}
              </h4>
              <p className="text-xs text-text-soft">
                Saved on this run · {linkedDetail.data.inputs.length} declared inputs
              </p>
              <PublicationBody
                analysis={linkedDetail.data}
                subject={{ kind: "run", run_id: runId }}
                projectId={projectId}
                collectionNames={collectionNames}
                onOpenRun={openInput}
                restorePosition={restorePosition}
              />
            </>
          )}
        </section>
      )}
      {query.isPending && <p>Reading analyses…</p>}
      {query.isError && <p role="alert">{errorMessage(query.error)}</p>}
      {query.isSuccess && !items.length && !linked && (
        <p className="text-xs text-text-dim">No analyses saved or directly linked yet.</p>
      )}
      {items
        .filter((item) => item.record.id !== linkedId)
        .map((item) => (
          <RelatedAnalysis
            key={`${projectId}:${item.record.id}`}
            summary={item}
            projectId={projectId}
            collectionNames={collectionNames}
            onOpenRun={openInput}
            restorePosition={restorePosition}
            expanded={expanded.includes(item.record.id)}
            onExpanded={(open) =>
              remember(
                open
                  ? [...new Set([...expanded, item.record.id])]
                  : expanded.filter((id) => id !== item.record.id),
              )
            }
          />
        ))}
      {query.hasNextPage && (
        <button
          className={secondaryButton}
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          Load older analyses
        </button>
      )}
    </section>
  );
}

function RelatedAnalysis({
  summary,
  expanded,
  onExpanded,
  ...context
}: RelatedContext & { summary: Summary; expanded: boolean; onExpanded: (open: boolean) => void }) {
  const subject = summary.subject;
  const id = summary.record.id;
  const detail = useQuery({
    queryKey: ["analysis", "publication", context.projectId, id],
    queryFn: ({ signal }) =>
      subject.kind === "run"
        ? getRunAnalysis(subject.run_id, id, signal)
        : subject.kind === "sample"
          ? getSampleAnalysis(subject.sample_id, id, signal)
          : getProjectAnalysis(id, signal),
    enabled: expanded,
  });
  const restorePosition = context.restorePosition;
  useEffect(() => {
    if (expanded && !detail.isPending) restorePosition?.();
  }, [expanded, detail.isPending, restorePosition]);
  return (
    <details
      className="min-w-0 rounded-md border border-line p-2"
      open={expanded}
      onToggle={(event) => {
        if (event.currentTarget.open !== expanded) onExpanded(event.currentTarget.open);
      }}
    >
      <summary className="cursor-pointer text-sm break-words">
        <strong>{summary.title}</strong>
        <span className="block text-xs text-text-dim">
          Saved on{" "}
          <PublicationSource
            subject={subject}
            projectId={context.projectId}
            collectionNames={context.collectionNames}
          />{" "}
          · revision {summary.revision} · {summary.input_count} declared inputs
        </span>
      </summary>
      {expanded &&
        (detail.isPending ? (
          <p>Reading analysis…</p>
        ) : detail.isError ? (
          <p role="alert">{errorMessage(detail.error)}</p>
        ) : (
          <PublicationBody analysis={detail.data} subject={subject} {...context} />
        ))}
    </details>
  );
}

function PublicationBody({
  analysis,
  subject,
  ...context
}: RelatedContext & { analysis: AnalysisPublication; subject: Summary["subject"] }) {
  const restorePosition = context.restorePosition;
  useEffect(() => {
    restorePosition?.();
  }, [restorePosition]);
  const exact =
    subject.kind === "run"
      ? `?run=${encodeURIComponent(subject.run_id)}&run-analysis=${encodeURIComponent(analysis.id)}#runs`
      : subject.kind === "sample"
        ? `?sample=${encodeURIComponent(subject.sample_id)}&sample-analysis=${encodeURIComponent(analysis.id)}#samples`
        : `?analysis=${encodeURIComponent(analysis.id)}#analyses`;
  const direct = [
    ...new Set(
      analysis.inputs
        .filter(
          (input) =>
            input.kind === "measurement_dataset" || input.kind === "configuration_snapshot",
        )
        .map((input) => input.run_id),
    ),
  ];
  return (
    <div className="mt-3 min-w-0 space-y-3">
      <AnalysisPublicationView
        analysis={analysis}
        onOpenRun={context.onOpenRun}
        copyTarget={
          subject.kind === "run"
            ? { kind: "run", runId: subject.run_id }
            : subject.kind === "sample"
              ? undefined
              : { kind: "project" }
        }
        getArtifactDownload={(selector) =>
          subject.kind === "run"
            ? getRunArtifactDownload(subject.run_id, selector)
            : subject.kind === "sample"
              ? getSampleAnalysisArtifactDownload(subject.sample_id, analysis.id, selector)
              : getProjectAnalysisArtifactDownload(analysis.id, selector)
        }
      />
      <div
        className="flex flex-wrap items-center gap-2 text-xs text-text-soft"
        aria-label="Direct run inputs"
      >
        <span>Direct run inputs:</span>
        {direct.length === 0 ? (
          <span>None declared</span>
        ) : (
          direct.map((id) => (
            <button
              key={id}
              className={secondaryButton}
              title={id}
              onClick={() => context.onOpenRun?.(id)}
            >
              <RunSource
                runId={id}
                projectId={context.projectId}
                collectionNames={context.collectionNames}
              />
            </button>
          ))
        )}
      </div>
      <a href={exact} onClick={navigateLink} className="text-sm text-accent underline">
        Open saved version
      </a>
      {subject.kind === "run" && analysis.subject === "run" && (
        <RepeatRunAnalysis runId={subject.run_id} analysis={{ ...analysis, subject: "run" }} />
      )}
    </div>
  );
}

function PublicationSource({
  subject,
  projectId,
  collectionNames,
}: Pick<RelatedContext, "projectId" | "collectionNames"> & { subject: Summary["subject"] }) {
  if (subject.kind === "run")
    return (
      <RunSource runId={subject.run_id} projectId={projectId} collectionNames={collectionNames} />
    );
  if (subject.kind === "sample")
    return <SampleSource sampleId={subject.sample_id} projectId={projectId} />;
  return <>Data space</>;
}

function RunSource({
  runId,
  projectId,
  collectionNames,
}: Pick<RelatedContext, "projectId" | "collectionNames"> & { runId: string }) {
  const run = useQuery({
    queryKey: ["run", "related-analysis-source", projectId, runId],
    queryFn: ({ signal }) => getRun(runId, signal),
  });
  const address = run.data?.address;
  const collection = useRunCollectionName(projectId, address?.collection_id, collectionNames);
  const name = run.data?.displayName ?? run.data?.experimentId;
  return (
    <span title={`Run ${runId}`}>
      {address
        ? `${collection.name ?? "Collection name unavailable"} #${address.number}`
        : (name ?? "Run name unavailable")}
      {address && name ? ` · ${name}` : ""}
    </span>
  );
}

function SampleSource({ sampleId, projectId }: { sampleId: string; projectId: string }) {
  const sample = useQuery({
    queryKey: ["sample", "related-analysis-source", projectId, sampleId],
    queryFn: ({ signal }) => getSample(sampleId, signal),
  });
  return (
    <span title={`Sample ${sampleId}`}>
      Sample {sample.data?.revision.content.display_name ?? "name unavailable"}
    </span>
  );
}
