import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function ProcedureWorkerLog({ procedureId }: { procedureId: string }) {
  const [expanded, setExpanded] = useState(false);
  const log = useQuery({
    queryKey: ["procedure-worker-log", procedureId],
    enabled: expanded,
    retry: false,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/procedures/{procedure_run_id}/worker-log", {
          params: { path: { procedure_run_id: procedureId }, query: { max_bytes: 16384 } },
          signal,
        }),
      ),
  });
  const data = log.isError ? undefined : log.data;
  return (
    <details className="space-y-2" onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>Recent worker output</summary>
      {expanded && (
        <>
          <p>This execution's process output; it does not establish the measurement outcome.</p>
          <button
            type="button"
            disabled={log.isFetching}
            onClick={() => {
              void log.refetch();
            }}
          >
            Refresh worker output
          </button>
          {log.isFetching && <p role="status">Reading worker output…</p>}
          {log.error && <p role="alert">{log.error.message}</p>}
          {data && (
            <>
              {!data.available ? (
                <p>No worker log has been created for this execution.</p>
              ) : (
                <>
                  <p>
                    {data.truncated
                      ? "Showing only the latest 16 KiB; earlier output is omitted."
                      : "Showing all output read."}{" "}
                    File size at read: {data.total_bytes} bytes.
                  </p>
                  <p>
                    Refresh to read new output. Use the displayed log path for the complete file.
                  </p>
                  {data.text ? (
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded border p-3 text-xs">
                      {data.text}
                    </pre>
                  ) : (
                    <p>The worker log is empty.</p>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
    </details>
  );
}
