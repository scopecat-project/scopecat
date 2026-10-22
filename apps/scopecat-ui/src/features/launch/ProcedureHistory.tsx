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
      className="border border-line rounded p-4 space-y-2"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="font-semibold">
        {mode === "submitted" ? "Submitted work" : "Experiment history"}
      </summary>
      {mode === "submitted" && (
        <p className="text-sm text-text-dim">
          Retained submissions, newest first. Waiting work is scheduled by resource availability;
          this list does not promise execution order. Select a task to inspect its progress and
          actions alongside this list.
        </p>
      )}
      <p>
        Open previous experiments and multi-step procedures to view their saved data. Opening
        history does not start another acquisition.
      </p>
      {history.isPending && <p role="status">Loading experiment history…</p>}
      {history.error && (
        <p role="alert">
          Submitted state unavailable; retained entries may be stale. {history.error.message}
        </p>
      )}
      {history.isSuccess && history.data.pages.every((page) => page.items.length === 0) && (
        <p>No experiments have been started yet.</p>
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
                className="underline text-left"
                type="button"
                aria-current={selectedId === procedure.procedure_run_id ? "true" : undefined}
                onClick={() => onSelect(procedure.procedure_run_id)}
              >
                {procedure.definition.id} ·{" "}
                {procedure.closure
                  ? stateLabel(procedure.closure.status)
                  : procedure.cancellation
                    ? "Cancellation requested"
                    : procedure.resource_wait
                      ? "Waiting for resources"
                      : stateLabel(procedure.state)}{" "}
                · {procedure.created_at} · {procedure.procedure_run_id}
              </button>
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
          Load earlier procedures
        </Controls.Button>
      )}
    </details>
  );
}
