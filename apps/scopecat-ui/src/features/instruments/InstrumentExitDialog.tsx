import { useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { useQueryClient } from "@tanstack/react-query";
import type { PendingMeasurement } from "./DirectMeasurements";
import { saveDirectMeasurement } from "./direct-measurement-api";
import { InstrumentFinishChoice } from "./InstrumentFinishChoice";
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
  onSaveAttempt,
  onCancel,
  onFinished,
}: {
  sessionId: string;
  pending: PendingMeasurement[];
  stagedCount: number;
  problem?: string;
  onSaved: (commandId: string, recordId?: string) => void;
  onSaveAttempt?: (item: PendingMeasurement) => void;
  onCancel: () => void;
  onFinished: () => void;
}) {
  const cache = useQueryClient();
  const saving = useRef(false);
  const [busy, setBusy] = useState(false);
  const [finishStarted, setFinishStarted] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = async (items: PendingMeasurement[]) => {
    if (saving.current) return;
    saving.current = true;
    setBusy(true);
    for (const item of items) {
      const id = item.selection.command_id;
      try {
        onSaveAttempt?.(item);
        const record = await saveDirectMeasurement(item.selection);
        onSaved(id, record.id);
        setErrors((current) => {
          const next = { ...current };
          delete next[id];
          return next;
        });
      } catch (error) {
        setErrors((current) => ({
          ...current,
          [id]: error instanceof Error ? error.message : String(error),
        }));
      }
    }
    void cache.invalidateQueries({ queryKey: ["direct-measurements"] });
    saving.current = false;
    setBusy(false);
  };
  const blocked = busy || (pending.length > 0 && !discard);
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open && !busy && !finishStarted) onCancel();
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
              保存测量、确认输出处置，再释放控制。取消会保留未保存结果和当前线路。
            </Dialog.Description>
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
                  会话结束后无法再保存这些结果。保存不会重新采集；部分失败时只重试仍未保存的项目。
                </p>
              )}
              {pending.map((item) => (
                <div
                  key={item.selection.command_id}
                  className="flex flex-wrap items-center gap-2 border-b border-line pb-2 text-xs"
                >
                  <span>
                    {item.selection.line_label || "未标注测量对象"} ·{" "}
                    {item.acquisition.label ?? item.acquisition.id} · {item.selection.note}
                  </span>
                  <button
                    type="button"
                    className={secondaryButton}
                    disabled={busy || discard || finishStarted}
                    onClick={() => void save([item])}
                  >
                    保存此结果
                  </button>
                  {errors[item.selection.command_id] && (
                    <p role="alert">
                      保存失败：{errors[item.selection.command_id]}。未保存结果仍保留，可重试。
                    </p>
                  )}
                </div>
              ))}
              {pending.length > 0 && (
                <>
                  <button
                    type="button"
                    className={secondaryButton}
                    disabled={busy || discard || finishStarted}
                    onClick={() => void save(pending)}
                  >
                    {busy ? "正在保存…" : "保存全部未保存结果"}
                  </button>
                  <label className="text-xs">
                    <input
                      type="checkbox"
                      checked={discard}
                      disabled={busy || finishStarted}
                      onChange={(event) => setDiscard(event.target.checked)}
                    />{" "}
                    明确放弃这 {pending.length} 条未保存结果（仅在结束控制后生效）
                  </label>
                </>
              )}
            </section>
            <InstrumentFinishChoice
              sessionId={sessionId}
              disabled={blocked}
              embedded
              onStarted={() => setFinishStarted(true)}
              onFinished={onFinished}
              onCancel={onCancel}
              cancelDisabled={busy}
            />
            {blocked && !busy && (
              <p className="text-xs">先保存或明确选择放弃，再选择下面的输出处置。</p>
            )}
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
