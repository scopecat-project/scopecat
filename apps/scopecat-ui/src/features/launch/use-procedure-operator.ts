import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

/** One cache shape shared by the compact status and full submitted-task view. */
export function useProcedureOperator(procedureId: string) {
  return useInfiniteQuery({
    queryKey: ["procedure-operator", procedureId],
    enabled: Boolean(procedureId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/procedures/{procedure_run_id}/operator", {
          params: { path: { procedure_run_id: procedureId }, query: { cursor: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.steps.next_cursor ?? undefined,
    refetchInterval: 1000,
  });
}
