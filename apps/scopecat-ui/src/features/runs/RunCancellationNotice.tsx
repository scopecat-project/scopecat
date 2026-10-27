import type { ProjectRun } from "../../types";

export function RunCancellationNotice({
  run,
}: {
  run: Pick<ProjectRun, "status" | "cancellationRequestedAt">;
}) {
  if (
    !run.cancellationRequestedAt ||
    ["succeeded", "failed", "cancelled", "attention_required", "closed"].includes(run.status)
  )
    return null;
  return (
    <p role="status">
      已请求取消运行，正在等待采集和清理结束。已采集的数据仍可查看；取消尚未完成，也不表示设备已物理停止。
    </p>
  );
}
