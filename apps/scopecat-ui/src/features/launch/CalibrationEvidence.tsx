import { useState } from "react";
import type { MethodResponse } from "openapi-fetch";
import { apiClient, apiData } from "../../api-client";

type TaskView = MethodResponse<typeof apiClient, "get", "/api/v1/calibration-tasks/{task_id}">;
type Stage = TaskView["task"]["specification"]["plan"]["stages"][number];
type Report = MethodResponse<typeof apiClient, "post", "/api/v1/calibration-checks/report">;

const statuses = {
  usable: "Usable evidence",
  out_of_spec: "Out of specification",
  recheck: "New check needed",
  unknown: "Unknown",
};
const reasons: Record<string, string> = {
  latest_matching: "Selected the latest matching measurement.",
  no_matching_evidence: "No check matches this exact scope and measurement context.",
  ambiguous_latest: "Equally recent evidence has conflicting authority; inspect the records.",
  incomplete_history: "The inspected history cannot establish a result.",
  scan_limit:
    "More checks exist than the history limit allows. Increase the limit to inspect more.",
  unresolved_checks: "Some checks have no resolved evidence. Inspect their executions below.",
  check_expired: "The measurement is older than the requested maximum age.",
  within_spec: "The selected check passed its scientific criterion.",
  out_of_spec: "The selected check did not meet its scientific criterion.",
  analysis_missing: "The scientific result is missing.",
  measurement_incomplete: "The measurement did not complete successfully.",
  evidence_from_future: "The measurement time is in the future; check clock consistency.",
  parameters_unsaved: "The measurement does not use an exact saved parameter revision.",
  subject_unbound: "A physical measurement subject is not bound.",
};

export function CalibrationEvidence({
  stage,
  onProcedure,
}: {
  stage: Stage;
  onProcedure: (id: string) => void;
}) {
  const [hours, setHours] = useState("");
  const [limit, setLimit] = useState("50");
  const [report, setReport] = useState<Report>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const seconds = Number(hours) * 3600;
  const historyLimit = Number(limit);
  const valid =
    Number.isFinite(seconds) &&
    seconds > 0 &&
    Number.isInteger(historyLimit) &&
    historyLimit >= 1 &&
    historyLimit <= 200;
  async function inspect() {
    if (!valid) return;
    setPending(true);
    setReport(undefined);
    setError("");
    try {
      setReport(
        await apiData(
          apiClient.POST("/api/v1/calibration-checks/report", {
            body: {
              context: stage.check.context,
              history_limit: historyLimit,
              requirements: [{ id: stage.id, scope: stage.check.scope, max_age: `PT${seconds}S` }],
            },
          }),
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  const result = report?.items[0];
  const evidence = result?.selection.evidence;
  return (
    <details className="space-y-2">
      <summary>Inspect applicable evidence for {stage.id}</summary>
      <p>
        Uses this stage's frozen parameters, subject, setup and scope, across retained tasks. This
        does not assess the current branch head or overall sample readiness.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void inspect();
        }}
      >
        <fieldset disabled={pending} className="flex flex-wrap gap-3 items-end">
          <label>
            Maximum evidence age (hours)
            <input
              className="block border rounded p-1"
              type="number"
              step="any"
              required
              value={hours}
              onChange={(event) => {
                setHours(event.target.value);
                setReport(undefined);
                setError("");
              }}
            />
          </label>
          <label>
            History limit
            <input
              className="block border rounded p-1"
              type="number"
              min="1"
              max="200"
              step="1"
              required
              value={limit}
              onChange={(event) => {
                setLimit(event.target.value);
                setReport(undefined);
                setError("");
              }}
            />
          </label>
          <button type="submit" disabled={!valid || pending}>
            Check evidence for {stage.id}
          </button>
        </fieldset>
      </form>
      {pending && <p role="status">Reading retained evidence…</p>}
      {error && <p role="alert">{error}</p>}
      {report && result && (
        <section aria-label={`Evidence report for ${stage.id}`} className="space-y-1">
          <p className="font-semibold">{statuses[result.selection.status]}</p>
          <p>
            Evaluated at {report.observed_at}. Read {result.scanned} checks. Refresh by checking
            again; this result does not update automatically.
          </p>
          <ul>
            {[
              result.selection.reason,
              ...(result.selection.assessment?.reasons ?? []),
              ...result.incomplete_reasons,
            ].map((reason) => (
              <li key={reason}>{reasons[reason] ?? reason.replaceAll("_", " ")}</li>
            ))}
          </ul>
          {evidence && (
            <>
              <p>Measurement created at {evidence.measurement.created_at}.</p>
              <a
                className="underline"
                href={`?run=${encodeURIComponent(evidence.measurement.run_id)}#runs`}
              >
                Open selected measurement
              </a>
              {evidence.analysis_record_id && (
                <a
                  className="underline block"
                  href={`?run=${encodeURIComponent(evidence.measurement.run_id)}&run-analysis=${encodeURIComponent(evidence.analysis_record_id)}#runs`}
                >
                  Open selected analysis
                </a>
              )}
            </>
          )}
          {result.unresolved_procedures.map((id) => (
            <button
              type="button"
              className="underline block"
              key={id}
              onClick={() => onProcedure(id)}
            >
              Inspect unresolved execution {id}
            </button>
          ))}
        </section>
      )}
    </details>
  );
}
