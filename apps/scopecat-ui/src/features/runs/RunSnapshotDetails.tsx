import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { formatCoordinateValue } from "../inspections/coordinate-codec";
import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { apiClient, apiData } from "../../api-client";

/** A read of the selected run, never the current editor or latest configuration. */
export function RunSnapshotDetails({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const retained = useQuery({
    queryKey: ["run-retained-input", runId],
    enabled: open,
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: runId } }, signal }),
      ),
    staleTime: Infinity,
  });
  const evidence = retained.data;
  const subject = evidence?.snapshot.scientific_binding.subject;
  const samples =
    subject?.kind === "registered_target"
      ? [subject.sample]
      : subject?.kind === "inline_samples"
        ? subject.samples
        : [];
  const plan = evidence?.control.admission.plan;
  return (
    <details
      className="rounded-md border border-line p-3"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-sm font-semibold">Frozen execution inputs</summary>
      <p className="mt-2 text-sm text-text-dim">
        Read-only inputs retained for this run. Draft edits do not change these references or its
        scan.
      </p>
      {open && retained.isPending && <p role="status">Loading retained inputs…</p>}
      {retained.error && <p role="alert">Retained inputs unavailable: {retained.error.message}</p>}
      {evidence && (
        <div className="mt-3 grid min-w-0 gap-3 text-sm">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="font-semibold">Configuration</dt>
              <dd className="break-words">{configurationLabel(evidence.snapshot.config_source)}</dd>
            </div>
            <div>
              <dt className="font-semibold">Setup version</dt>
              <dd className="break-words">
                {evidence.snapshot.execution_setup?.revision_id ?? "Not reported"}
              </dd>
            </div>
            <div>
              <dt className="font-semibold">Experiment</dt>
              <dd>{plan?.experiment_id}</dd>
            </div>
            <div>
              <dt className="font-semibold">Accepted scan</dt>
              <dd>
                {plan?.point_count != null
                  ? `${plan.point_count.toLocaleString()} planned points`
                  : `Adaptive scan · ${plan?.initial_point_count.toLocaleString()} initial points`}
              </dd>
            </div>
          </dl>
          {subject?.kind === "registered_target" && (
            <p>
              Target: {subject.ref.target_id} · revision {subject.ref.revision}
            </p>
          )}
          {samples.length > 0 ? (
            <ul aria-label="Retained samples">
              {samples.map((sample) => (
                <li key={`${sample.role}:${sample.sample_id}`}>
                  {sample.display_name} · {sample.role} · revision {sample.revision}
                </li>
              ))}
            </ul>
          ) : (
            <p>No sample binding retained.</p>
          )}
          {(plan?.coordinates.length ?? 0) > 0 && (
            <ul aria-label="Scan coordinates" className="space-y-2">
              {plan?.coordinates.map((axis) => (
                <li key={axis.id} className="break-words">
                  <strong>
                    {axis.id}
                    {axis.unit ? ` (${axis.unit})` : ""}
                  </strong>
                  :{" "}
                  {axis.sampled_values.length
                    ? axis.sampled_values.map(formatCoordinateValue).join(", ")
                    : "No sampled values retained"}
                  {axis.sampled_values_truncated ? " … (sample only)" : ""}
                  {plan.adaptive_coordinate_ids.includes(axis.id) ? " · adaptive" : ""}
                </li>
              ))}
            </ul>
          )}
          <ExecutionScenario
            scenario={evidence.snapshot.scientific_binding.scenario}
            label="Retained execution scenario"
          />
          <details className="text-xs text-text-dim">
            <summary className="cursor-pointer">Exact retained identities</summary>
            <dl className="mt-2 space-y-2 break-all">
              <div>
                <dt>Configuration fingerprint</dt>
                <dd className="font-mono">{evidence.snapshot.config_content_hash}</dd>
              </div>
              <div>
                <dt>Setup fingerprint</dt>
                <dd className="font-mono">
                  {evidence.snapshot.scientific_binding.setup_content_hash}
                </dd>
              </div>
              <div>
                <dt>Scan fingerprint</dt>
                <dd className="font-mono">{plan?.point_plan_fingerprint}</dd>
              </div>
            </dl>
          </details>
        </div>
      )}
    </details>
  );
}

function configurationLabel(
  source: components["schemas"]["RunConfigSource-Output"] | null | undefined,
): string {
  if (!source) return "Source not reported";
  switch (source.kind) {
    case "config_registry":
      return `Saved configuration · ${source.entry_id}`;
    case "parameter_revision":
      return `Parameter version · ${source.parameters.revision_id}${source.overrides.length ? ` · ${source.overrides.length} overrides` : ""}`;
    case "parameter_context":
      return `Saved context · ${source.context.entry_id}${source.overrides.length ? ` · ${source.overrides.length} overrides` : ""}`;
    case "analysis_candidate":
      return `Analysis candidate · ${source.proposal_id} · from run ${source.source_run_id}`;
  }
}
