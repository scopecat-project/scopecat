import { measurementKey } from "./measurement-identity";
import type { MeasurementSave } from "./direct-measurement-api";
import { useEffect, useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { useQueryClient } from "@tanstack/react-query";
import type { PendingMeasurement } from "./DirectMeasurements";
import { discardDirectMeasurement, saveDirectMeasurement } from "./direct-measurement-api";
import { InstrumentFinishChoice } from "./InstrumentFinishChoice";
import {
  useInstrumentFinishRecovery,
  type InstrumentFinishRecovery,
} from "./instrument-finish-recovery";
import { InstrumentRequestWaitExpired, waitForInstrumentResponse } from "./instrument-request-wait";
import {
  classes,
  dialogBackdrop,
  dialogPopup,
  dialogTitle,
  dialogViewport,
  secondaryButton,
} from "../../ui/styles";

/** Window close and explicit release share one review surface. Nothing is discarded on cancel. */
export function InstrumentExitDialog({
  sessionId,
  pending,
  stagedCount,
  problem,
  onSaved,
  onDiscarded,
  onForgotten,
  onSaveAttempt,
  onCancel,
  onFinished,
  finishRecovery,
  sessionUnavailable = false,
  recoveryCloseAllowed = false,
  recoveryUnresolved = false,
  onRecoveryFinished,
  closeWaiting = false,
  reviewRequest = 0,
  pendingCheckBlocked = false,
  onRetryPendingCheck,
}: {
  sessionId: string;
  pending: PendingMeasurement[];
  stagedCount: number;
  problem?: string;
  onSaved: (commandId: string, recordId?: string, selection?: MeasurementSave) => void;
  onDiscarded?: (commandId: string, selection?: MeasurementSave) => void;
  onForgotten?: (commandId: string, selection?: MeasurementSave) => void;
  onSaveAttempt?: (item: PendingMeasurement) => void;
  onCancel: () => void;
  onFinished: () => void;
  finishRecovery?: InstrumentFinishRecovery;
  sessionUnavailable?: boolean;
  recoveryCloseAllowed?: boolean;
  recoveryUnresolved?: boolean;
  onRecoveryFinished?: () => void;
  closeWaiting?: boolean;
  reviewRequest?: number;
  pendingCheckBlocked?: boolean;
  onRetryPendingCheck?: () => void;
}) {
  const cache = useQueryClient();
  const [disposed, setDisposed] = useState<Set<string>>(new Set());
  pending = pending.filter((item) => !disposed.has(measurementKey(item.selection)));
  const [notRetained, setNotRetained] = useState<Set<string>>(new Set());
  const attempts = useRef<Record<string, number>>({});
  const saving = useRef(false);
  const savingItem = useRef<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const localRecovery = useInstrumentFinishRecovery(sessionId);
  const recovery = finishRecovery ?? localRecovery;
  const finishStarted = !!recovery.state && recovery.state.phase !== "rejected";
  const finishPending = recovery.state?.phase === "pending" && !recoveryCloseAllowed;
  const batch = useRef(0);
  useEffect(
    () => () => {
      batch.current += 1;
    },
    [],
  );
  const [discardReview, setDiscardReview] = useState<number>();
  const [acknowledgedReview, setAcknowledgedReview] = useState<number>();
  const discard = discardReview === reviewRequest;
  const acknowledgedUnknown = acknowledgedReview === reviewRequest;
  const lastReview = useRef(reviewRequest);
  useEffect(() => {
    if (lastReview.current !== reviewRequest) {
      lastReview.current = reviewRequest;
      recovery.cancelWaiting();
    }
  }, [reviewRequest, recovery]);
  const recoveryNeedsAcknowledgement =
    recoveryUnresolved ||
    recovery.state?.phase === "pending" ||
    recovery.state?.phase === "unknown";
  const [errors, setErrors] = useState<Record<string, string>>({});
  const cancelWaiting = () => {
    const id = savingItem.current;
    if (saving.current && id) {
      setErrors((current) => ({
        ...current,
        [id]: "已停止等待；原保存请求可能仍在处理；已提交的放弃请求也不会撤销",
      }));
    }
    batch.current += 1;
    saving.current = false;
    setBusy(false);
    recovery.cancelWaiting();
  };
  useEffect(() => {
    window.addEventListener("scopecat:device-close-cancelled", cancelWaiting);
    return () => window.removeEventListener("scopecat:device-close-cancelled", cancelWaiting);
  });
  const cancel = () => {
    cancelWaiting();
    onCancel();
  };
  const save = async (items: PendingMeasurement[], discarding = false) => {
    if (saving.current) return;
    const sequence = ++batch.current;
    saving.current = true;
    setBusy(true);
    for (const item of items) {
      if (batch.current !== sequence) break;
      const id = measurementKey(item.selection);
      const attempt = (attempts.current[id] ?? 0) + 1;
      attempts.current[id] = attempt;
      setNotRetained((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
      try {
        if (!discarding) onSaveAttempt?.(item);
        savingItem.current = id;
        const request = discarding
          ? discardDirectMeasurement(item.selection)
          : saveDirectMeasurement(item.selection).then((record) => ({
              status: "saved" as const,
              record_id: record.id,
            }));
        // Even after timeout/dismissal a saved receipt must update its original result.
        void request.then(
          (record) => {
            if (record.status === "not_retained") {
              if (attempts.current[id] === attempt)
                setNotRetained((current) => new Set(current).add(id));
              return;
            }
            if (record.status === "saved")
              onSaved(item.selection.command_id, record.record_id ?? undefined, item.selection);
            else onDiscarded?.(item.selection.command_id, item.selection);
            setDisposed((current) => new Set(current).add(id));
            setErrors((current) => {
              const next = { ...current };
              delete next[id];
              return next;
            });
            void cache.invalidateQueries({ queryKey: ["direct-measurements"] });
          },
          () => undefined,
        );
        await waitForInstrumentResponse(request);
        if (batch.current === sequence) savingItem.current = undefined;
      } catch (error) {
        if (batch.current !== sequence) break;
        setErrors((current) => ({
          ...current,
          [id]: error instanceof Error ? error.message : String(error),
        }));
        // Do not start more writes after a hung request. An explicit retry keeps attribution.
        if (error instanceof InstrumentRequestWaitExpired) break;
      }
    }
    if (batch.current === sequence) {
      saving.current = false;
      setBusy(false);
    }
  };
  const blocked = busy || pendingCheckBlocked || pending.length > 0;
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) cancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup
            className={classes(
              dialogPopup,
              "grid max-h-[calc(100dvh-2rem)] w-[min(720px,100%)] gap-3 overflow-y-auto p-4",
            )}
          >
            <Dialog.Title className={dialogTitle}>结束控制前的待办</Dialog.Title>
            <Dialog.Description className="text-xs">
              保存测量、确认输出处置，再释放控制。取消不会放弃尚未提交处置的结果，也不会改变当前线路。
            </Dialog.Description>
            {pendingCheckBlocked && (
              <div className="grid gap-2">
                <p role="status">
                  后台待保存结果尚未核查完成，暂不能结束控制或关闭窗口；本页已有结果仍保留。
                </p>
                <button type="button" className={secondaryButton} onClick={onRetryPendingCheck}>
                  重新核查待保存结果
                </button>
              </div>
            )}
            {problem && <p role="alert">待核查：{problem}</p>}
            {stagedCount > 0 && (
              <p className="text-xs">
                还有 {stagedCount} 项未发送的设置草稿；结束会话不会应用它们。
              </p>
            )}
            <section className="grid gap-2" aria-label="保存测量待办">
              <h3 className="text-sm font-semibold">未保存结果：{pending.length}</h3>
              {pending.length > 0 && (
                <p className="text-xs">
                  只要后台服务仍在，控制会话结束后仍可保存或明确放弃结果；后台重启后不保证恢复。保存不会重新采集，部分失败时只重试仍未确认保存的项目。
                </p>
              )}
              {pending.map((item) => (
                <div
                  key={measurementKey(item.selection)}
                  className="flex flex-wrap items-center gap-2 border-b border-line pb-2 text-xs"
                >
                  <span>
                    {item.selection.line_label || "未标注测量对象"} ·{" "}
                    {item.acquisition.label ?? item.acquisition.id} · {item.selection.note}
                  </span>
                  <button
                    type="button"
                    className={secondaryButton}
                    disabled={busy || discard || finishPending}
                    onClick={() => void save([item])}
                  >
                    保存此结果
                  </button>
                  {notRetained.has(measurementKey(item.selection)) && (
                    <div className="grid gap-2">
                      <p role="status">
                        后台已核查，但找不到此结果的待保存回执、已保存记录或放弃记录。后台重启后的结果恢复不受支持。
                        本页仍保留该结果；移除本页待办不会保存结果，也不代表后台已成功放弃结果。
                      </p>
                      <button
                        type="button"
                        className={secondaryButton}
                        disabled={busy || finishPending || !onForgotten}
                        onClick={() => {
                          const id = measurementKey(item.selection);
                          onForgotten?.(item.selection.command_id, item.selection);
                          setDisposed((current) => new Set(current).add(id));
                        }}
                      >
                        仅从本页移除此无法恢复的结果
                      </button>
                    </div>
                  )}
                  {errors[measurementKey(item.selection)] && (
                    <p role="alert">
                      保存或放弃尚未确认：{errors[measurementKey(item.selection)]}
                      。原结果和归属仍保留；重试只处置同一结果，不会重新采集。
                    </p>
                  )}
                </div>
              ))}
              {pending.length > 0 && (
                <>
                  <button
                    type="button"
                    className={secondaryButton}
                    disabled={busy || discard || finishPending}
                    onClick={() => void save(pending)}
                  >
                    {busy ? "正在处置…" : "保存全部未保存结果"}
                  </button>
                  <label className="text-xs">
                    <input
                      type="checkbox"
                      checked={discard}
                      disabled={busy || finishPending}
                      onChange={(event) =>
                        setDiscardReview(event.target.checked ? reviewRequest : undefined)
                      }
                    />{" "}
                    明确放弃这 {pending.length} 条未保存结果（需另行提交确认）
                  </label>
                  {discard && (
                    <button
                      type="button"
                      className={secondaryButton}
                      disabled={busy || finishPending}
                      onClick={() => void save(pending, true)}
                    >
                      确认放弃所选结果
                    </button>
                  )}
                </>
              )}
            </section>
            {sessionUnavailable && (!finishStarted || recoveryCloseAllowed) ? (
              <section className="grid gap-2" aria-label="恢复结束确认">
                <p className="text-xs">
                  原控制会话不可用。保存仍使用原结果身份；只有已核实后台会话已结束且没有正在执行的动作，才能确认待办并仅关闭窗口。历史未知结果仍保留，关闭窗口不保证物理输出已停止。
                </p>
                {recoveryNeedsAcknowledgement && (
                  <label className="text-xs">
                    <input
                      type="checkbox"
                      checked={acknowledgedUnknown}
                      onChange={(event) =>
                        setAcknowledgedReview(event.target.checked ? reviewRequest : undefined)
                      }
                    />{" "}
                    我理解历史设备动作结果仍未知，关闭窗口不会确认或重发动作
                  </label>
                )}
                <button
                  type="button"
                  className={secondaryButton}
                  disabled={
                    blocked ||
                    !recoveryCloseAllowed ||
                    !onRecoveryFinished ||
                    (recoveryNeedsAcknowledgement && !acknowledgedUnknown)
                  }
                  onClick={() => {
                    cancelWaiting();
                    onRecoveryFinished?.();
                  }}
                >
                  确认待办并关闭窗口
                </button>
                <button type="button" className={secondaryButton} onClick={cancel}>
                  返回工作台（保留待核查状态）
                </button>
              </section>
            ) : (
              <InstrumentFinishChoice
                sessionId={sessionId}
                disabled={blocked}
                embedded
                finishRecovery={recovery}
                sessionUnavailable={sessionUnavailable}
                onFinished={onFinished}
                onCancel={cancel}
              />
            )}
            {closeWaiting && (
              <button
                type="button"
                className={secondaryButton}
                onClick={() => {
                  cancelWaiting();
                  window.dispatchEvent(new Event("scopecat:cancel-close-wait"));
                }}
              >
                取消本次关闭等待
              </button>
            )}
            {(busy || finishStarted) && (
              <p className="text-xs">
                取消等待仅撤销退出意图，不会撤销已提交的保存或收尾动作；迟到回执仍保留。
              </p>
            )}
            {blocked && !busy && <p className="text-xs">先保存或明确选择放弃，再确认退出待办。</p>}
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
