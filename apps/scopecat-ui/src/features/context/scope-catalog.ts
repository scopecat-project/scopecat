import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function useScopeCatalog(kind: "batch" | "collection", owner?: string, available = true) {
  const path = kind === "batch" ? "/api/v1/experimental-batches" : "/api/v1/record-collections";
  return useInfiniteQuery({
    queryKey: ["context-catalog", owner, kind],
    enabled: available,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET(path, {
          params: { query: { limit: 100, before: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}
