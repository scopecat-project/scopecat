import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { errorMessage } from "../../lib/presentation";
import { getRunAnalysis, getRunArtifactDownload } from "../runs/run-api";
import { getSampleAnalysis, getSampleAnalysisArtifactDownload } from "../samples/sample-api";
import { getProjectAnalysis, getProjectAnalysisArtifactDownload } from "./analysis-api";
import { AnalysisPublicationView } from "./AnalysisPublicationView";

type Summary = components["schemas"]["AnalysisPublicationSummary"];

export function RelatedAnalyses({ runId }: { runId: string }) {
  const query = useInfiniteQuery({
    queryKey: ["analyses", "related", runId],
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
        <RelatedAnalysis key={item.record.id} summary={item} />
      ))}
      {query.hasNextPage && (
        <button
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

function RelatedAnalysis({ summary }: { summary: Summary }) {
  const [expanded, setExpanded] = useState(false);
  const subject = summary.subject;
  const id = summary.record.id;
  const detail = useQuery({
    queryKey: ["analysis", "publication", id],
    queryFn: ({ signal }) =>
      subject.kind === "run"
        ? getRunAnalysis(subject.run_id, id, signal)
        : subject.kind === "sample"
          ? getSampleAnalysis(subject.sample_id, id, signal)
          : getProjectAnalysis(id, signal),
    enabled: expanded,
  });
  const source =
    subject.kind === "run"
      ? `Run ${subject.run_id}`
      : subject.kind === "sample"
        ? `Sample ${subject.sample_id}`
        : "Data space";
  return (
    <details
      className="rounded-md border border-line p-2"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-sm">
        <strong>{summary.title}</strong>
        <span className="block text-xs text-text-dim">
          {source} · revision {summary.revision} · {summary.input_count} inputs
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
            onOpenRun={(run) => {
              window.location.href = `/?run=${encodeURIComponent(run)}#runs`;
            }}
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
