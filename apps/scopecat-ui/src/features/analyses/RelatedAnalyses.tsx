import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { errorMessage } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";
import { useRunCollectionName } from "../runs/use-run-collection-name";
import { getRun, getRunAnalysis, getRunArtifactDownload } from "../runs/run-api";
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
}

export function RelatedAnalyses({
  runId,
  projectId,
  collectionNames,
  onOpenRun,
}: RelatedContext & { runId: string }) {
  const query = useInfiniteQuery({
    queryKey: ["analyses", "related", projectId, runId],
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/analysis-publications", {
          params: { query: { input_run_id: runId, limit: 100, before: pageParam } },
          signal,
        }),
      ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = [
    ...new Map(
      query.data?.pages.flatMap((page) => page.items ?? []).map((item) => [item.record.id, item]),
    ).values(),
  ];
  return (
    <section className="grid gap-2 rounded-lg border border-line p-4" aria-label="Related analyses">
      <h3 className="m-0 text-sm font-semibold">Related analyses</h3>
      <p className="m-0 text-xs text-text-dim">
        Results that directly use this run’s measurements or configuration, wherever saved.
      </p>
      {query.isPending && <p>Reading related analyses…</p>}
      {query.isError && <p role="alert">{errorMessage(query.error)}</p>}
      {query.isSuccess && items.length === 0 && (
        <p className="text-xs text-text-dim">No related analyses yet.</p>
      )}
      {items.map((item) => (
        <RelatedAnalysis
          key={`${projectId}:${item.record.id}`}
          summary={item}
          projectId={projectId}
          collectionNames={collectionNames}
          onOpenRun={onOpenRun}
        />
      ))}
      {query.hasNextPage && (
        <button
          className={secondaryButton}
          type="button"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          Load older related analyses
        </button>
      )}
    </section>
  );
}

function RelatedAnalysis({
  summary,
  projectId,
  collectionNames,
  onOpenRun,
}: RelatedContext & { summary: Summary }) {
  const [expanded, setExpanded] = useState(false);
  const subject = summary.subject;
  const id = summary.record.id;
  const detail = useQuery({
    queryKey: ["analysis", "publication", projectId, id],
    queryFn: ({ signal }) =>
      subject.kind === "run"
        ? getRunAnalysis(subject.run_id, id, signal)
        : subject.kind === "sample"
          ? getSampleAnalysis(subject.sample_id, id, signal)
          : getProjectAnalysis(id, signal),
    enabled: expanded,
  });
  return (
    <details
      className="rounded-md border border-line p-2"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-sm">
        <strong>{summary.title}</strong>
        <span className="block text-xs text-text-dim">
          <PublicationSource
            subject={subject}
            projectId={projectId}
            collectionNames={collectionNames}
          />{" "}
          · revision {summary.revision} · {summary.input_count} inputs
        </span>
      </summary>
      {expanded &&
        (detail.isPending ? (
          <p>Reading analysis…</p>
        ) : detail.isError ? (
          <p role="alert">{errorMessage(detail.error)}</p>
        ) : (
          <AnalysisPublicationView
            analysis={detail.data}
            onOpenRun={onOpenRun}
            getArtifactDownload={(selector) =>
              subject.kind === "run"
                ? getRunArtifactDownload(subject.run_id, selector)
                : subject.kind === "sample"
                  ? getSampleAnalysisArtifactDownload(subject.sample_id, id, selector)
                  : getProjectAnalysisArtifactDownload(id, selector)
            }
          />
        ))}
    </details>
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
