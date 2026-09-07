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
  if (query.error) return <p role="status">Failure evidence unavailable: {query.error.message}</p>;
  return query.data ? <RunFailureEvidenceContent evidence={query.data} /> : null;
}

export function RunFailureEvidenceContent({
  evidence,
}: {
  evidence: components["schemas"]["RunFailureEvidence"];
}) {
  if (!evidence.primary) return null;
  return (
    <section className="rounded border border-line p-4 space-y-2" aria-label="Run failure evidence">
      <h3 className="font-semibold">Operation failed</h3>
      <p>{evidence.primary.message}</p>
      {evidence.terminal_persistence === "unconfirmed" && (
        <p>Terminal persistence is unconfirmed. Review the saved run state before continuing.</p>
      )}
      {evidence.secondary && evidence.secondary.length > 0 && (
        <details>
          <summary>Additional failure evidence ({evidence.secondary.length})</summary>
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
              View retained diagnostics
              {diagnostic.instrument_id ? ` — ${diagnostic.instrument_id}` : ""}
              {diagnostic.operation ? ` / ${diagnostic.operation}` : ""}
            </a>
          ) : (
            <>Diagnostics not retained: all worker retention slots were active.</>
          )}
        </p>
      ))}
      {evidence.diagnostics && evidence.diagnostics.length > 0 && (
        <p className="text-sm">
          Diagnostics retain a bounded prefix; later output may be discarded.
        </p>
      )}
      {evidence.truncated && <p>Only the latest saved evidence window is shown.</p>}
    </section>
  );
}
