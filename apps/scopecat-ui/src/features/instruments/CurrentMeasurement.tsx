import type { InstrumentCollectReceipt } from "../../api-contract";
import type { InstrumentAcquisitionTarget } from "./instrument-api";
import type { MeasurementSave } from "./direct-measurement-api";
import { AcquisitionCurve } from "./AcquisitionCurve";

export interface CurrentMeasurementResult {
  target: InstrumentAcquisitionTarget;
  receipt: InstrumentCollectReceipt;
  selection: MeasurementSave;
  receivedAt: string;
  relationshipChanged?: string;
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
      <h3 className="text-sm font-semibold">当前测量结果</h3>
      <p className="text-xs">
        {action} · {selection.line_label || "未标注测量对象"} · 返回于{" "}
        {new Date(result.receivedAt).toLocaleString()}
      </p>
      <p className="text-xs text-text-dim">
        {target.interfaceId} / {target.componentPath.join(" / ") || "设备"} /{" "}
        {target.acquisition.id}
      </p>
      <p className="text-xs text-text-dim">
        只显示最近一次成功返回的结果，不表示不同动作对应同一条物理曲线。坐标及生成设置以该回执证据为准；返回时间不是曲线生成时间。
      </p>
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
      <AcquisitionCurve receipt={receipt} acquisition={target.acquisition} />
    </section>
  );
}
