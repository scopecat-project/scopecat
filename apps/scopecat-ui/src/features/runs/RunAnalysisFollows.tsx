import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { secondaryButton } from "../../ui/styles";

export function RunAnalysisFollows({ runId }: { runId: string }) {
  const client = useQueryClient();
  const queryKey = ["analysis-follows", runId];
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}/analysis-follows", {
          params: { path: { run_id: runId } },
          signal,
        }),
      ),
    refetchInterval: 2000,
  });
  const progress = query.data
    ?.map((item) => `${item.request.id}:${item.state}:${item.finished_count}`)
    .join(";");
  useEffect(() => {
    if (progress) void client.invalidateQueries({ queryKey: ["analyses", "run", runId] });
  }, [client, runId, progress]);
  const stop = useMutation({
    mutationFn: (identity: string) =>
      apiData(
        apiClient.POST("/api/v1/analysis-follows/{identity}/stop", {
          params: { path: { identity } },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey }),
  });
  if (query.error) return <p role="status">Live analysis unavailable: {query.error.message}</p>;
  if (!query.data?.length) return null;
  return (
    <section aria-label="Live group analysis" className="rounded border border-line p-4 space-y-2">
      <h3 className="font-semibold">Live group analysis</h3>
      {query.data.map((item) => (
        <div key={item.request.id} className="space-y-1">
          <strong>{item.request.analysis.analysis}</strong>
          {item.state === "running" && (
            <p>
              {item.active_group == null
                ? "Waiting for complete groups"
                : `Analyzing group ${item.active_group + 1}`}
            </p>
          )}
          <p role="status">
            {item.state} · {item.finished_count} / {item.group_count ?? "…"} groups ·{" "}
            {item.failed_count} need inspection
          </p>
          {item.error && <p>{item.error}</p>}
          {item.state === "running" && (
            <button
              className={secondaryButton}
              type="button"
              disabled={stop.isPending}
              onClick={() => stop.mutate(item.request.id)}
            >
              Stop analysis
            </button>
          )}
        </div>
      ))}
      {query.data.some((item) => item.state === "running") && (
        <p>Completed results appear below. Stopping analysis leaves acquisition running.</p>
      )}
      {query.data.length === 100 && (
        <p>Showing the latest 100 follows. Older progress remains available by its saved id.</p>
      )}
      {stop.error && <p role="alert">{stop.error.message}</p>}
    </section>
  );
}
