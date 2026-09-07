import type { components } from "../../api-schema";
import { normalizeRun } from "../runs/run-api";
import { RunCancellationNotice } from "../runs/RunCancellationNotice";

export function ProcedureChildRun({
  child,
  procedureId,
  current = false,
}: {
  child: components["schemas"]["ProcedureChildRunView"];
  procedureId: string;
  current?: boolean;
}) {
  const run = normalizeRun(child.run.control, child.run.snapshot, child.run.resources);
  return (
    <div className="space-y-1">
      <a
        className="underline"
        href={`?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(run.runId)}#runs`}
      >
        Open {current ? "current child run" : "retained run"}: {run.displayName ?? run.experimentId}
      </a>
      <p>
        {run.stateLabel} · {run.progressCompleted ?? 0} completed points · Result:{" "}
        {run.result ?? "pending"} · Certainty: {run.certainty ?? "pending"}
      </p>
      <RunCancellationNotice run={run} />
      {run.attentionReason && <p>{run.attentionReason}</p>}
      {child.run.resources
        .filter((resource) => resource.blocked_by)
        .map((resource) => (
          <p key={resource.resource.id}>
            Waiting for {resource.resource.id}: held by{" "}
            {resource.blocked_by?.owner_kind === "run" ? (
              <a
                className="underline"
                href={`?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(resource.blocked_by.owner_id)}#runs`}
              >
                another run
              </a>
            ) : (
              "an instrument session"
            )}
            {resource.blocked_by?.status === "quarantined" ? " (quarantined)" : ""}.
          </p>
        ))}
    </div>
  );
}
