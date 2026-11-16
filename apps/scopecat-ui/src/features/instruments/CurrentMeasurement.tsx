import type { InstrumentCollectReceipt } from "../../api-contract";
import type { InstrumentAcquisitionTarget } from "./instrument-api";
import type { MeasurementSave } from "./direct-measurement-api";
import { unavailableResults, unavailableReasonLabels } from "./InstrumentInterfaceControls";
import { AcquisitionCurve } from "./AcquisitionCurve";

export interface CurrentMeasurementResult {
  target: InstrumentAcquisitionTarget;
  receipt: InstrumentCollectReceipt;
  selection: MeasurementSave;
  receivedAt: string;
  relationshipChanged?: string;
  savedRecordId?: string;
}

export function CurrentMeasurement({
  result,
  staged,
  sessionId,
}: {
  result: CurrentMeasurementResult;
  staged: boolean;
  sessionId?: string;
}) {
  const { target, receipt, selection } = result;
  const unavailable = unavailableResults(receipt);
  const action =
    target.interfaceId === "scopecat.network_sweep/v1" && target.acquisition.id === "sweep"
      ? "新采集"
      : target.interfaceId === "scopecat.existing_network_trace/v1" &&
          target.acquisition.id === "read_trace"
        ? "读取已有曲线（未触发新采集）"
        : (target.acquisition.label ?? target.acquisition.id);
  return (
    <section
      aria-label="当前测量结果"
      className="grid min-w-0 gap-2 rounded border border-line p-3"
    >
      <h3 className="text-sm font-semibold">{selection.title || "当前测量结果"}</h3>
      <p className="text-xs">
        {action} · {selection.line_label || "未标注测量对象"} · 返回于{" "}
        {new Date(result.receivedAt).toLocaleString()}
      </p>
      <p className="text-xs text-text-dim">
        {target.interfaceId} / {target.componentPath.join(" / ") || "设备"} /{" "}
        {target.acquisition.id}
      </p>
      <p className="text-xs text-text-dim">回执中的坐标与设置</p>
      {sessionId !== selection.session_id && (
        <p className="text-xs text-yellow">结果来自此前的控制会话；不是当前设备状态的证明。</p>
      )}
      {result.relationshipChanged && (
        <p className="text-xs text-yellow">
          {result.relationshipChanged}；保留原曲线，不能用当前设置解释其生成条件。
        </p>
      )}
      {staged && (
        <p className="text-xs text-yellow">
          有未发送的设置草稿，尚未应用到设备，也未改变这条曲线。
        </p>
      )}
      {unavailable.count > 0 && (
        <p role="status" className="text-xs text-yellow">
          {unavailable.count} 项结果不可用 · 原因：{" "}
          {unavailable.reasons.map((reason) => unavailableReasonLabels[reason]).join("、")}
        </p>
      )}
      <AcquisitionCurve receipt={receipt} acquisition={target.acquisition} />
    </section>
  );
}
