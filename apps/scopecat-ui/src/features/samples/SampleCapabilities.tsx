import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { ProjectRun } from "../../types";
import { CalibrationProfiles } from "../launch/CalibrationProfiles";

export function SampleCapabilities({
  sampleId,
  revision,
  runs,
}: {
  sampleId: string;
  revision: number;
  runs: Pick<ProjectRun, "runId" | "displayName" | "experimentId" | "createdAt" | "samples">[];
}) {
  const [runId, setRunId] = useState("");
  const choices = runs.filter((run) =>
    run.samples.some((sample) => sample.sample_id === sampleId && sample.revision === revision),
  );
  const query = useQuery({
    queryKey: ["sample-capability-context", sampleId, revision, runId],
    enabled: Boolean(runId),
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}", {
          params: { path: { run_id: runId } },
          signal,
        }),
      ),
  });
  const snapshot = query.isError ? undefined : query.data?.snapshot;
  const source = snapshot?.config_source;
  const belongs = snapshot?.samples.some(
    (sample) => sample.sample_id === sampleId && sample.revision === revision,
  );
  const context =
    snapshot && belongs && source?.kind === "parameter_revision" && source.overrides.length === 0
      ? {
          parameters: source.parameters,
          subject: snapshot.scientific_binding.subject,
          setup_content_hash: snapshot.scientific_binding.setup_content_hash,
          scenario: snapshot.scientific_binding.scenario ?? null,
        }
      : undefined;
  return (
    <details className="border rounded p-3 space-y-3">
      <summary>Capability evidence for this sample revision</summary>
      <p>
        Select a measurement to reuse its exact saved parameters, setup, subject and scenario. This
        inspects a historical context, not the current parameter branch or overall sample readiness.
        Joint measurements retain their complete subject, including other samples.
      </p>
      <label>
        Measurement context
        <select value={runId} onChange={(event) => setRunId(event.target.value)}>
          <option value="">Choose a measurement</option>
          {choices.map((run) => (
            <option key={run.runId} value={run.runId}>
              {run.displayName || run.experimentId} · {run.createdAt ?? "time unavailable"} ·{" "}
              {run.runId}
            </option>
          ))}
        </select>
      </label>
      {!choices.length && (
        <p>No loaded measurements for this sample revision. Load older runs below if available.</p>
      )}
      {runId && query.isPending && <p role="status">Reading measurement context…</p>}
      {query.error && (
        <>
          <p role="alert">{query.error.message}</p>
          <button
            type="button"
            disabled={query.isFetching}
            onClick={() => {
              void query.refetch();
            }}
          >
            Retry reading context
          </button>
        </>
      )}
      {snapshot && !context && (
        <p role="alert">
          This measurement does not provide an exact saved parameter context for this sample
          revision. Measurements with parameter overrides or candidate configurations cannot be used
          here.
        </p>
      )}
      {context && (
        <section key={runId} aria-label="Selected capability context">
          <p>
            Parameters: {context.parameters.revision_id} · Setup: {context.setup_content_hash}
          </p>
          <p>
            Scenario:{" "}
            {context.scenario
              ? `${context.scenario.id} (${context.scenario.model_id}, ${context.scenario.model_version})`
              : "physical"}
          </p>
          <details>
            <summary>Complete measurement subject</summary>
            <pre>{JSON.stringify(context.subject, null, 2)}</pre>
          </details>
          <CalibrationProfiles
            context={context}
            contextDescription={`Uses the frozen context of measurement ${runId}.`}
          />
        </section>
      )}
    </details>
  );
}
