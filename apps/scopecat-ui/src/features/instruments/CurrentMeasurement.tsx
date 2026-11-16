import { useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import {
  secondaryButton,
  dialogBackdrop,
  dialogViewport,
  dialogPopup,
  dialogTitle,
} from "../../ui/styles";
import {
  ReceiptValidationNotice,
  OriginalReceiptDownload,
  isVerifiedReceipt,
  type ReceiptValidation,
} from "./ReceiptValidation";
import type { InstrumentAcquisitionTarget } from "./instrument-api";
import type { MeasurementSave } from "./direct-measurement-api";
import { BoundedMeasurement, type MeasurementDisplayStore } from "./BoundedMeasurement";
import { measurementKey } from "./measurement-identity";

export interface CurrentMeasurementResult extends ReceiptValidation {
  target: InstrumentAcquisitionTarget;
  selection: MeasurementSave;
  receivedAt: string;
  relationshipChanged?: string;
  savedRecordId?: string;
}

export function CurrentMeasurement({
  result,
  staged,
  sessionId,
  displayStore,
  workspace = false,
}: {
  workspace?: boolean;
  result: CurrentMeasurementResult;
  staged: boolean;
  sessionId?: string;
  displayStore?: MeasurementDisplayStore;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const { target, selection } = result;
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
      className={
        workspace
          ? "device-current-measurement"
          : "grid min-w-0 max-w-full grid-cols-1 gap-2 rounded border border-line p-3 [overflow-wrap:anywhere]"
      }
    >
      {workspace ? (
        <div className="device-result-summary">
          <span>
            {action} · {selection.line_label || "未标注测量对象"} ·{" "}
            {new Date(result.receivedAt).toLocaleTimeString()}
          </span>
          <button className={secondaryButton} onClick={() => setDetailsOpen(true)}>
            结果详情
          </button>
        </div>
      ) : (
        <>
          {" "}
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
        </>
      )}
      <div className={workspace ? "device-result-notices" : undefined}>
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
        <ReceiptValidationNotice value={result} />
        {!workspace && result.savedRecordId && (
          <OriginalReceiptDownload recordId={result.savedRecordId} />
        )}
      </div>
      {isVerifiedReceipt(result) && (
        <BoundedMeasurement
          source={result.savedRecordId ? { recordId: result.savedRecordId } : { selection }}
          acquisition={target.acquisition}
          displayStore={displayStore}
          displayKey={measurementKey(selection)}
          workspace={workspace}
        />
      )}
      {workspace && (
        <Dialog.Root open={detailsOpen} onOpenChange={setDetailsOpen}>
          <Dialog.Portal>
            <Dialog.Backdrop className={dialogBackdrop} />
            <Dialog.Viewport className={dialogViewport}>
              <Dialog.Popup className={`${dialogPopup} max-h-[85dvh] overflow-auto p-4`}>
                <Dialog.Title className={dialogTitle}>结果详情</Dialog.Title>
                <Dialog.Description>
                  {selection.title || "当前测量结果"} · {action}
                </Dialog.Description>
                <p className="my-2 text-xs">
                  返回于 {new Date(result.receivedAt).toLocaleString()}
                </p>
                <p className="my-2 break-words text-xs">
                  {target.interfaceId} / {target.componentPath.join(" / ") || "设备"} /{" "}
                  {target.acquisition.id}
                </p>
                <p className="my-2 text-xs">回执中的坐标与设置；保存使用完整原始数据。</p>
                {result.savedRecordId && (
                  <OriginalReceiptDownload recordId={result.savedRecordId} />
                )}
                <Dialog.Close className={`${secondaryButton} mt-3`}>关闭详情</Dialog.Close>
              </Dialog.Popup>
            </Dialog.Viewport>
          </Dialog.Portal>
        </Dialog.Root>
      )}
    </section>
  );
}
