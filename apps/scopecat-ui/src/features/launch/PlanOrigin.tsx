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
    <section aria-label="Experiment plan origin" className="border rounded p-2">
      <p>
        From saved plan {plan.data?.name ?? reference.plan_id}, revision {reference.revision}.
      </p>
      <a
        className="underline"
        href={`?plan=${encodeURIComponent(reference.plan_id)}&plan_revision=${reference.revision}&plan_hash=${encodeURIComponent(reference.content_hash)}#launch`}
      >
        Reopen exact plan
      </a>
      {plan.data?.definition.source && (
        <a
          className="underline ml-2"
          href={`?compare=${encodeURIComponent(plan.data.definition.source.run_id)}&comparison-analysis=${encodeURIComponent(plan.data.definition.source.analysis_id)}#analyses`}
        >
          Source analysis
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
        <section
          aria-label="Decision source"
          className="min-w-0 rounded border border-line p-3 text-sm"
        >
          <p>
            Started from this saved conclusion. Submitted inputs may differ from its suggestion.
          </p>
          <a
            className="text-accent underline"
            onClick={navigateLink}
            href={`?run=${encodeURIComponent(source.run_id)}&run-analysis=${encodeURIComponent(source.analysis_id)}#runs`}
          >
            Open exact source conclusion
          </a>
          <details>
            <summary>Exact source identity</summary>
            <p className="break-all">
              Run {source.run_id} · Publication {source.analysis_id} · {source.publication_hash}
            </p>
          </details>
        </section>
      )}
      {request.error && <p role="alert">Decision source unavailable: {request.error.message}</p>}
    </>
  );
}
