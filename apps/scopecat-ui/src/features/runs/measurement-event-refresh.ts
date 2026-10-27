import type { QueryClient, QueryFilters } from "@tanstack/react-query";

/** Keep notification bursts from cancelling a useful measurement read repeatedly. */
export function measurementEventRefresh(client: QueryClient) {
  const lanes = new Map<string, { pending: boolean; generation: number }>();
  let generation = 0;
  let disposed = false;

  const invalidate = (filters: QueryFilters) => {
    if (disposed) return;
    // Inactive identities must remain stale for their next mount, without fetching them.
    void client.invalidateQueries({ ...filters, refetchType: "none" });
    for (const query of client.getQueryCache().findAll({ ...filters, type: "active" })) {
      const existing = lanes.get(query.queryHash);
      if (existing) {
        existing.pending = true;
        continue;
      }
      const lane = { pending: true, generation };
      lanes.set(query.queryHash, lane);
      void (async () => {
        try {
          do {
            lane.pending = false;
            await client.refetchQueries(
              { queryKey: query.queryKey, exact: true, type: "active" },
              { cancelRefetch: false },
            );
            // Any number of notifications during a read requests one fresh snapshot next.
            if (disposed || lane.generation !== generation) break;
          } while (lane.pending);
        } finally {
          if (lanes.get(query.queryHash) === lane) lanes.delete(query.queryHash);
        }
      })();
    }
  };

  const reset = () => {
    generation += 1;
    lanes.clear();
  };
  return {
    invalidate(runId: string) {
      invalidate({ queryKey: ["measurements", runId], exact: true });
      invalidate({ queryKey: ["measurements", "live", runId], exact: true });
      invalidate({ queryKey: ["measurement-slice", runId] });
      invalidate({ queryKey: ["measurement-trace", runId] });
    },
    reset,
    dispose() {
      disposed = true;
      reset();
    },
  };
}
