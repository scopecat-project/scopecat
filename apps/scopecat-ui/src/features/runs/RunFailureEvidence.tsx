import { useQuery } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { apiClient, apiData } from "../../api-client";

export function RunFailureEvidence({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: ["run-failure-evidence", runId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}/failure-evidence", {
          params: { path: { run_id: runId } },
        }),
      ),
    refetchInterval: 2000,
  });
  if (query.error) return <p role="status">无法获取失败证据： {query.error.message}</p>;
  return query.data ? <RunFailureEvidenceContent evidence={query.data} /> : null;
}

export function RunFailureEvidenceContent({
  evidence,
}: {
  evidence: components["schemas"]["RunFailureEvidence"];
}) {
  if (!evidence.primary) return null;
  return (
    <section className="rounded border border-line p-4 space-y-2" aria-label="运行失败证据">
      <h3 className="font-semibold">操作失败</h3>
      <p>{evidence.primary.message}</p>
      {evidence.terminal_persistence === "unconfirmed" && (
        <p>尚未确认终态是否已持久保存。请先核查已保存的运行状态，再继续。</p>
      )}
      {evidence.secondary && evidence.secondary.length > 0 && (
        <details>
          <summary>其他失败证据（{evidence.secondary.length}）</summary>
          <ul>
            {evidence.secondary.map((problem, index) => (
              <li key={problem.occurrence_id ?? `${problem.code}-${index}`}>{problem.message}</li>
            ))}
          </ul>
        </details>
      )}
      {(evidence.diagnostics ?? []).map((diagnostic, index) => (
        <p key={`${diagnostic.generation}-${diagnostic.request_id ?? index}`}>
          {diagnostic.href ? (
            <a className="underline" href={diagnostic.href} target="_blank" rel="noreferrer">
              查看已保留的诊断
              {diagnostic.instrument_id ? ` — ${diagnostic.instrument_id}` : ""}
              {diagnostic.operation ? ` / ${diagnostic.operation}` : ""}
            </a>
          ) : (
            <>诊断未保留：所有 Worker 诊断保留槽位均被活动任务占用。</>
          )}
        </p>
      ))}
      {evidence.diagnostics && evidence.diagnostics.length > 0 && (
        <p className="text-sm">诊断仅保留长度受限的开头部分，后续输出可能已被丢弃。</p>
      )}
      {evidence.truncated && <p>仅显示最近保存的部分证据。</p>}
    </section>
  );
}
