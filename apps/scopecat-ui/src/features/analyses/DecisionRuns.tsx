import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { getRun } from "../runs/run-api";
import { RunAddressLabel } from "../runs/RunAddressLabel";
import { useRunCollectionName } from "../runs/use-run-collection-name";
import { secondaryButton } from "../../ui/styles";

export function DecisionRuns({
  projectId,
  runId,
  analysisId,
  publicationHash,
  onOpenRun,
}: {
  projectId?: string;
  runId: string;
  analysisId: string;
  publicationHash: string;
  onOpenRun?: (id: string) => void;
}) {
  const query = useInfiniteQuery({
    queryKey: ["decision-runs", projectId, runId, analysisId, publicationHash],
    enabled: Boolean(projectId),
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}/analyses/{analysis_id}/decision-runs", {
          params: {
            path: { run_id: runId, analysis_id: analysisId },
            query: { publication_hash: publicationHash, limit: 50, before: pageParam },
          },
          signal,
        }),
      ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const ids = [...new Set(query.data?.pages.flatMap((page) => page.run_ids))];
  return (
    <section
      aria-label="以此结论为来源的后续实验"
      className="min-w-0 rounded border border-line p-3 text-sm"
    >
      <h4>以此结论为来源的后续实验</h4>
      <p className="text-xs text-text-dim">这里只记录决策来源；这些后续运行不是本分析的输入。</p>
      {query.isPending && <p>正在读取后续实验…</p>}
      {query.error && <p role="alert">后续实验不可用： {query.error.message}</p>}
      {query.isSuccess && !ids.length && <p>暂无已接纳的运行。</p>}
      <div className="flex flex-wrap gap-2">
        {ids.map((id) => (
          <DecisionRun key={id} id={id} projectId={projectId!} onOpenRun={onOpenRun} />
        ))}
      </div>
      {query.hasNextPage && (
        <button
          className={secondaryButton}
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          加载更早的后续实验
        </button>
      )}
    </section>
  );
}
function DecisionRun({
  id,
  projectId,
  onOpenRun,
}: {
  id: string;
  projectId: string;
  onOpenRun?: (id: string) => void;
}) {
  const run = useQuery({
    queryKey: ["run", "decision-source", projectId, id],
    queryFn: ({ signal }) => getRun(id, signal),
  });
  const collection = useRunCollectionName(projectId, run.data?.address?.collection_id, new Map());
  return (
    <button title={id} className={secondaryButton} onClick={() => onOpenRun?.(id)}>
      {run.data ? (
        <RunAddressLabel
          address={run.data.address}
          sequence={run.data.sequence}
          collectionName={collection.name}
        />
      ) : (
        id
      )}
    </button>
  );
}
