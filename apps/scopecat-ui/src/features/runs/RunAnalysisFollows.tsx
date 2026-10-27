import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { secondaryButton } from "../../ui/styles";

const followState = {
  running: "进行中",
  completed: "处理完成",
  attention: "需要检查",
  stopped: "已停止",
} as const;

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
    if (progress) {
      void client.invalidateQueries({ queryKey: ["analyses", "run", runId] });
      void client.invalidateQueries({ queryKey: ["analyses", "related"] });
    }
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
  if (query.error) return <p role="status">实时分析不可用： {query.error.message}</p>;
  if (!query.data?.length) return null;
  return (
    <section aria-label="实时分组分析" className="rounded border border-line p-4 space-y-2">
      <h3 className="font-semibold">实时分组分析</h3>
      {query.data.map((item) => (
        <div key={item.request.id} className="space-y-1">
          <strong>{item.request.analysis.analysis}</strong>
          {item.state === "running" && (
            <p>
              {item.active_group == null
                ? "等待完整分组"
                : `正在分析第 ${item.active_group + 1} 组`}
            </p>
          )}
          <p role="status">
            {followState[item.state]} · {item.finished_count} / {item.group_count ?? "…"} 组 ·{" "}
            {item.failed_count} 组需要检查
          </p>
          {item.error && <p>{item.error}</p>}
          {item.state === "running" && (
            <button
              className={secondaryButton}
              type="button"
              disabled={stop.isPending}
              onClick={() => stop.mutate(item.request.id)}
            >
              停止分析
            </button>
          )}
        </div>
      ))}
      {query.data.some((item) => item.state === "running") && (
        <p>已完成的结果显示在下方。停止分析不会停止采集。</p>
      )}
      {query.data.length === 100 && (
        <p>显示最近 100 个实时分组分析任务。更早的进度可通过已保存的任务 ID 读取。</p>
      )}
      {stop.error && <p role="alert">{stop.error.message}</p>}
    </section>
  );
}
