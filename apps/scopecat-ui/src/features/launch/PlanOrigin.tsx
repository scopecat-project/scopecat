import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { navigateLink } from "../../lib/navigation";
import type { PlanRef } from "./experiment-plans";

export function PlanOrigin({ reference }: { reference?: PlanRef | null }) {
  const plan = useQuery({
    queryKey: ["experiment-plan-origin", reference],
    enabled: Boolean(reference),
    queryFn: () => apiData(apiClient.POST("/api/v1/experiment-plans/read", { body: reference! })),
  });
  if (!reference) return null;
  return (
    <section aria-label="实验计划来源" className="border rounded p-2">
      <p>
        来自已保存计划 {plan.data?.name ?? reference.plan_id}，修订 {reference.revision}。
      </p>
      <a
        className="underline"
        href={`?plan=${encodeURIComponent(reference.plan_id)}&plan_revision=${reference.revision}&plan_hash=${encodeURIComponent(reference.content_hash)}#launch`}
      >
        重新打开此精确计划
      </a>
      {plan.data?.definition.source && (
        <a
          className="underline ml-2"
          href={`?compare=${encodeURIComponent(plan.data.definition.source.run_id)}&comparison-analysis=${encodeURIComponent(plan.data.definition.source.analysis_id)}#analyses`}
        >
          来源分析
        </a>
      )}
      {plan.error && <p role="alert">{plan.error.message}</p>}
    </section>
  );
}

export function RunPlanOrigin({ runId, projectId }: { runId: string; projectId?: string }) {
  const request = useQuery({
    queryKey: ["run-request", projectId, runId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}/request", { params: { path: { run_id: runId } } }),
      ),
  });
  const source = request.data?.request.decision_source;
  return (
    <>
      <PlanOrigin reference={request.data?.plan_ref} />
      {source && (
        <section aria-label="判断来源" className="min-w-0 rounded border border-line p-3 text-sm">
          <p>由此已保存结论发起。提交的输入可能与其建议不同。</p>
          <a
            className="text-accent underline"
            onClick={navigateLink}
            href={`?run=${encodeURIComponent(source.run_id)}&run-analysis=${encodeURIComponent(source.analysis_id)}#runs`}
          >
            打开精确来源结论
          </a>
          <details>
            <summary>精确来源身份</summary>
            <p className="break-all">
              运行 {source.run_id} · 发布记录 {source.analysis_id} · {source.publication_hash}
            </p>
          </details>
        </section>
      )}
      {request.error && <p role="alert">判断来源不可用： {request.error.message}</p>}
    </>
  );
}
