import { AlertTriangle, Unlock } from "lucide-react";
import { errorMessage } from "../../lib/presentation";
import type { ProjectRun } from "../../types";
import { useMeasurementFocus } from "./MeasurementWorkspace";
import { ManualLineRecovery } from "./ManualLineRecovery";

export function RunAttentionNotice({
  run,
  onResolve,
  pending,
  error,
}: {
  run: Pick<ProjectRun, "status" | "attentionReason"> &
    Partial<
      Pick<ProjectRun, "runId" | "runContractFingerprint" | "manualLineConfirmation" | "configHash">
    >;
  onResolve: () => void;
  pending: boolean;
  error: Error | null;
}) {
  const focused = useMeasurementFocus();
  if (run.status !== "attention_required") return null;
  return (
    <div
      className="mt-[18px] flex items-start gap-[11px] rounded-md border border-[rgb(237_201_111_/_23%)] bg-yellow-soft px-3.5 py-[13px]"
      role="alert"
    >
      <AlertTriangle className="flex-none text-yellow" size={19} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <strong className="text-sm text-yellow">Operator attention required</strong>
        <p className="mt-1 mb-0 text-xs leading-normal text-text-soft">
          {run.attentionReason ?? "The daemon has not reported a reconciliation reason."}
        </p>
        {!focused &&
          run.attentionReason === "manual_line_confirmation_expired" &&
          run.manualLineConfirmation &&
          run.runId &&
          run.runContractFingerprint && (
            <ManualLineRecovery
              key={run.runId}
              runId={run.runId}
              fingerprint={run.runContractFingerprint}
              original={run.manualLineConfirmation}
              configHash={run.configHash}
            />
          )}
        {run.attentionReason !== "manual_line_confirmation_expired" && (
          <p className="mt-1 mb-0 text-xs leading-normal text-text-soft">
            Inspect retained data and reconcile external state using the project workflow. Unknown
            hardware effects cannot be retried here. Closing this record does not establish that the
            hardware is safe.
          </p>
        )}
        {!focused && run.attentionReason !== "manual_line_confirmation_expired" && (
          <>
            <div className="mt-[11px] flex flex-wrap gap-[7px]">
              <button
                className="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-[7px] border border-[rgb(255_140_136_/_35%)] bg-red-soft px-2.5 text-red hover:not-disabled:bg-panel-strong hover:not-disabled:text-text disabled:cursor-wait disabled:opacity-55"
                type="button"
                onClick={onResolve}
                disabled={pending}
              >
                <Unlock size={15} aria-hidden="true" />
                {pending ? "Closing…" : "Close without resuming"}
              </button>
            </div>
          </>
        )}
        {error && (
          <p className="mt-1 mb-0 text-xs leading-normal text-red" role="status">
            {errorMessage(error)}
          </p>
        )}
      </div>
    </div>
  );
}
