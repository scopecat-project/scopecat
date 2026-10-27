import * as Controls from "../../ui/Controls";
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
      <summary>最近的 Worker 输出</summary>
      {expanded && (
        <>
          <p>本次执行的进程输出，不作为测量结果的判定。</p>
          <Controls.Button
            type="button"
            disabled={log.isFetching}
            onClick={() => {
              void log.refetch();
            }}
          >
            刷新 Worker 输出
          </Controls.Button>
          {log.isFetching && <p role="status">正在读取 Worker 输出…</p>}
          {log.error && <p role="alert">{log.error.message}</p>}
          {data && (
            <>
              {!data.available ? (
                <p>本次执行尚未创建 Worker 日志。</p>
              ) : (
                <>
                  <p>
                    {data.truncated
                      ? "仅显示最后 16 KiB，已省略更早的输出。"
                      : "显示本次读取的全部输出。"}{" "}
                    读取时文件大小： {data.total_bytes} 字节。
                  </p>
                  <p>刷新可读取新输出。完整文件请查看所显示的日志路径。</p>
                  {data.text ? (
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded border p-3 text-xs">
                      {data.text}
                    </pre>
                  ) : (
                    <p>Worker 日志为空。</p>
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
