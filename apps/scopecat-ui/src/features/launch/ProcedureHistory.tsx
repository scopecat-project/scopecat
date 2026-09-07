import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { stateLabel } from "./procedure-operator";

export function ProcedureHistory({
  selectedId,
  onSelect,
}: {
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
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
      className="border rounded p-4 space-y-2"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="font-semibold">Retained procedures</summary>
      <p>Reopen admitted work and its results after a browser or daemon restart.</p>
      {history.isPending && <p role="status">Loading retained procedures…</p>}
      {history.error && <p role="alert">{history.error.message}</p>}
      <ul className="space-y-1">
        {history.data?.pages
          .flatMap((page) => page.items)
          .map((procedure) => (
            <li key={procedure.procedure_run_id}>
              <button
                className="underline text-left"
                type="button"
                aria-current={selectedId === procedure.procedure_run_id ? "true" : undefined}
                onClick={() => onSelect(procedure.procedure_run_id)}
              >
                {procedure.definition.id} ·{" "}
                {procedure.closure
                  ? stateLabel(procedure.closure.status)
                  : procedure.resource_wait
                    ? "Waiting for resources"
                    : stateLabel(procedure.state)}{" "}
                · {procedure.created_at}
              </button>
            </li>
          ))}
      </ul>
      {history.hasNextPage && (
        <button
          type="button"
          disabled={history.isFetchingNextPage}
          onClick={() => {
            void history.fetchNextPage();
          }}
        >
          Load earlier procedures
        </button>
      )}
    </details>
  );
}
