import { withDraftContext } from "./draft-location";
import { RunAddressLabel } from "../runs/RunAddressLabel";
import { navigateLink } from "../../lib/navigation";
import { OpenRunWindow } from "../runs/OpenRunWindow";
import type { ProcedureOperatorView } from "../../api-contract";
import { normalizeRun } from "../runs/run-api";
import { RunCancellationNotice } from "../runs/RunCancellationNotice";

export function ProcedureChildRun({
  child,
  procedureId,
  current = false,
  collectionName,
}: {
  child: ProcedureOperatorView["child_runs"][number];
  procedureId: string;
  current?: boolean;
  collectionName?: string;
}) {
  const run = normalizeRun(
    child.run.control,
    child.run.snapshot,
    child.run.resources,
    child.run.address,
  );
  return (
    <div className="space-y-1">
      <a
        className="underline"
        href={withDraftContext(
          `?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(run.runId)}#runs`,
        )}
        onClick={navigateLink}
      >
        View {current && !run.result ? "current data" : "saved data"}:{" "}
        {run.displayName ?? run.experimentId}
      </a>
      <RunAddressLabel
        address={run.address}
        sequence={run.sequence}
        collectionName={collectionName}
      />
      <p>
        {run.stateLabel} · {run.progressCompleted ?? 0} completed points
      </p>
      {run.certainty && run.certainty !== "known" && (
        <p role="alert">
          The acquisition outcome is uncertain. Inspect the available data and reconcile external
          state before starting another acquisition.
        </p>
      )}
      <details>
        <summary>Acquisition diagnostics</summary>
        <p>
          Result: {run.result ?? "pending"} · Certainty: {run.certainty ?? "pending"}
        </p>
      </details>
      <OpenRunWindow key={run.runId} runId={run.runId} />
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
                onClick={navigateLink}
                href={withDraftContext(
                  `?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(resource.blocked_by.owner_id)}#runs`,
                )}
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
