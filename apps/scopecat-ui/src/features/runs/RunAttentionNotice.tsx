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
        <strong className="text-sm text-yellow">需要人工处理</strong>
        <p className="mt-1 mb-0 text-xs leading-normal text-text-soft">
          {run.attentionReason ?? "服务尚未报告需要核对处理的原因。"}
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
            请检查已保留的数据，并按项目流程核对外部状态。硬件效果未知时不能在此重试。关闭此记录不代表硬件已安全。
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
                {pending ? "正在关闭…" : "关闭且不继续"}
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
