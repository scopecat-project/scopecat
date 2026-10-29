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
        查看{current && !run.result ? "当前结果" : "已保存结果"}:{" "}
        {run.displayName ?? run.experimentId}
      </a>
      <RunAddressLabel
        address={run.address}
        sequence={run.sequence}
        collectionName={collectionName}
      />
      <p>
        {run.stateLabel} · {run.completedLogicalPointCount ?? "未知"} 个已完成逻辑点
      </p>
      {run.certainty && run.certainty !== "known" && (
        <p role="alert">采集结果尚不确定。再次采集前，请检查已有数据并核实外部状态。</p>
      )}
      <details>
        <summary>采集诊断</summary>
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
            等待资源 {resource.resource.id}，占用者：{" "}
            {resource.blocked_by?.owner_kind === "run" ? (
              <a
                className="underline"
                onClick={navigateLink}
                href={withDraftContext(
                  `?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(resource.blocked_by.owner_id)}#runs`,
                )}
              >
                其他 run
              </a>
            ) : (
              "设备会话"
            )}
            {resource.blocked_by?.status === "quarantined" ? "（已隔离）" : ""}.
          </p>
        ))}
    </div>
  );
}
