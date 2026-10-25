import * as Controls from "../../ui/Controls";
import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { stateLabel } from "./procedure-operator";

export function ProcedureHistory({
  selectedId,
  onSelect,
  mode = "history",
}: {
  mode?: "history" | "submitted";
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(mode === "submitted");
  const history = useInfiniteQuery({
    queryKey: ["procedure-history"],
    enabled: expanded,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/procedures", {
          params: { query: { limit: 20, cursor: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 3000,
  });
  return (
    <details
      open={mode === "submitted" || undefined}
      className="min-w-0 border border-line rounded p-3 space-y-2"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="font-semibold">
        {mode === "submitted" ? "已提交任务" : "实验历史"}
      </summary>
      {mode === "submitted" && (
        <div className="text-xs text-text-dim">
          <p>按新到旧排列；查看任务不会启动采集。</p>
          <details>
            <summary className="cursor-pointer">调度与检查</summary>
            <p>等待任务按资源可用性调度，并非按此列表排序。选择任务可在列表旁查看进度和操作。</p>
          </details>
        </div>
      )}
      {history.isPending && <p role="status">正在加载实验历史…</p>}
      {history.error && (
        <p role="alert">无法更新提交状态；保留记录可能已过时。 {history.error.message}</p>
      )}
      {history.isSuccess && history.data.pages.every((page) => page.items.length === 0) && (
        <p>尚未启动实验。</p>
      )}
      <ul className="space-y-1">
        {history.data?.pages
          .flatMap((page) => page.items)
          .map((procedure) => (
            <li
              key={procedure.procedure_run_id}
              className="border-b border-line py-2 last:border-0"
            >
              <button
                className="grid w-full min-w-0 gap-1 text-left hover:bg-panel-strong aria-current:bg-panel-strong focus-visible:outline-accent"
                type="button"
                aria-current={selectedId === procedure.procedure_run_id ? "true" : undefined}
                onClick={() => onSelect(procedure.procedure_run_id)}
              >
                <span className="font-medium">
                  {procedure.definition.id} ·{" "}
                  {procedure.closure
                    ? stateLabel(procedure.closure.status)
                    : procedure.cancellation
                      ? "已请求取消"
                      : procedure.resource_wait
                        ? "等待资源"
                        : stateLabel(procedure.state)}{" "}
                </span>
                <span className="truncate text-xs text-text-dim" title={procedure.procedure_run_id}>
                  <span aria-hidden="true">…{procedure.procedure_run_id.slice(-8)}</span>
                  <span className="sr-only">{procedure.procedure_run_id}</span>
                </span>
              </button>
              <details className="text-xs text-text-dim">
                <summary className="cursor-pointer">任务标识与时间</summary>
                <p className="select-text [overflow-wrap:anywhere]">{procedure.procedure_run_id}</p>
                <time dateTime={procedure.created_at}>{procedure.created_at}</time>
              </details>
              {procedure.resource_wait && !procedure.closure && (
                <p className="text-sm text-text-dim">
                  Waiting child: {procedure.resource_wait.run_id}. Select this task for resource
                  owners.
                </p>
              )}
              {procedure.attention_reason && (
                <p className="text-sm text-yellow">{procedure.attention_reason}</p>
              )}
            </li>
          ))}
      </ul>
      {history.hasNextPage && (
        <Controls.Button
          type="button"
          disabled={history.isFetchingNextPage}
          onClick={() => {
            void history.fetchNextPage();
          }}
        >
          加载更早任务
        </Controls.Button>
      )}
    </details>
  );
}
