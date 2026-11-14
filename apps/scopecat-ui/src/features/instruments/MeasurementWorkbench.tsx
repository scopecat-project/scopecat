import { SavedMeasurementLink } from "./SavedMeasurementView";
import { useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { useQueryClient } from "@tanstack/react-query";
import type {
  InstrumentDescription,
  InstrumentInterface,
  InstrumentCollectReceipt,
} from "../../api-contract";
import { CurrentMeasurement, type CurrentMeasurementResult } from "./CurrentMeasurement";
import {
  acquisitionKey,
  interfaceMountPaths,
  instrumentReceiptLabel,
} from "./InstrumentInterfaceControls";
import type { InstrumentAcquisitionTarget } from "./instrument-api";
import type { PendingMeasurement } from "./DirectMeasurements";
import { saveDirectMeasurement, type MeasurementSave } from "./direct-measurement-api";
import {
  classes,
  secondaryButton,
  primaryButton,
  fieldLabel,
  formControl,
  dialogBackdrop,
  dialogViewport,
  dialogPopup,
  dialogTitle,
} from "../../ui/styles";

export function acquisitionTargets(
  description?: InstrumentDescription,
): InstrumentAcquisitionTarget[] {
  const targets: InstrumentAcquisitionTarget[] = [];
  const visit = (
    member: Pick<InstrumentInterface, "acquisitions" | "components">,
    interfaceId: string,
    componentPath: string[],
  ) => {
    for (const acquisition of member.acquisitions ?? [])
      targets.push({ interfaceId, componentPath, acquisition });
    for (const child of member.components ?? [])
      visit(child, interfaceId, [...componentPath, child.id]);
  };
  for (const member of description?.interfaces ?? []) {
    for (const path of interfaceMountPaths(description!, member.id)) visit(member, member.id, path);
  }
  return targets;
}

export function MeasurementWorkbench({
  collapsed,
  onCollapsedChange,
  description,
  connected,
  disabled,
  collectingTarget,
  collectResults,
  recent,
  current,
  staged,
  sessionId,
  pending,
  saveDisabled = false,
  onCollect,
  onSelect,
  onSaveAttempt,
  onSaved,
}: {
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  description?: InstrumentDescription;
  connected: boolean;
  disabled: boolean;
  collectingTarget?: InstrumentAcquisitionTarget;
  collectResults: Record<string, InstrumentCollectReceipt>;
  recent: CurrentMeasurementResult[];
  current?: CurrentMeasurementResult;
  staged: boolean;
  sessionId?: string;
  pending: PendingMeasurement[];
  saveDisabled?: boolean;
  onCollect: (target: InstrumentAcquisitionTarget) => void;
  onSelect: (result: CurrentMeasurementResult) => void;
  onSaveAttempt: (selection: MeasurementSave) => void;
  onSaved: (commandId: string, recordId?: string) => void;
}) {
  const cache = useQueryClient();
  const [target, setTarget] = useState<CurrentMeasurementResult>();
  const [title, setTitle] = useState("");
  const [line, setLine] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const [error, setError] = useState("");
  const targets = acquisitionTargets(description);
  if (!targets.length && !current) return null;
  const commandId = current?.selection.command_id;
  const canSave = pending.some((item) => item.selection.command_id === commandId);
  const pendingTarget = pending.find(
    (item) => item.selection.command_id === target?.selection.command_id,
  );
  const frozen = pendingTarget?.saveAttempted ? pendingTarget.selection : undefined;
  const openSave = () => {
    if (!current || !canSave || saveDisabled) return;
    const selection =
      pending.find((item) => item.selection.command_id === current.selection.command_id)
        ?.selection ?? current.selection;
    setTarget(current);
    setTitle(selection.title ?? "");
    setLine(selection.line_label ?? "");
    setNote(selection.note ?? "");
    setError("");
  };
  const save = async () => {
    if (!target || saving.current) return;
    const id = target.selection.command_id;
    const selection = frozen ?? {
      ...target.selection,
      title: title.trim(),
      line_label: line.trim(),
      note: note.trim(),
    };
    if (
      (selection.title?.length ?? 0) > 500 ||
      (selection.line_label?.length ?? 0) > 500 ||
      (selection.note?.length ?? 0) > 4000
    ) {
      setError("标题与线路名称最多 500 字，备注最多 4000 字。");
      return;
    }
    onSaveAttempt(selection);
    saving.current = true;
    setBusy(true);
    setError("");
    try {
      const record = await saveDirectMeasurement(selection);
      onSaved(id, record.id);
      setTarget(undefined);
      void cache.invalidateQueries({ queryKey: ["direct-measurements"] });
    } catch (reason) {
      setError(
        `保存尚未确认：${reason instanceof Error ? reason.message : String(reason)}。重试将提交相同内容。`,
      );
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  return (
    <section
      aria-label="交互采集"
      className={classes(
        "min-w-0",
        collapsed ? "justify-self-start" : "rounded-md border border-line bg-panel-soft p-3",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        {!collapsed && <h3 className="text-sm font-semibold">采集</h3>}
        <button
          className={secondaryButton}
          onClick={() => onCollapsedChange(!collapsed)}
          aria-expanded={!collapsed}
        >
          {collapsed ? "展开采集面板" : "收起采集面板"}
        </button>
        {collapsed && pending.length > 0 && (
          <span className="text-xs text-text-dim">{pending.length} 条待保存</span>
        )}
      </div>
      <div hidden={collapsed}>
        <div className="my-3 flex flex-wrap gap-2">
          {targets.map((item) => (
            <div key={acquisitionKey(item)}>
              <button
                className={primaryButton}
                disabled={!connected || disabled}
                onClick={() => onCollect(item)}
                title={`${item.interfaceId} / ${item.componentPath.join("/")}`}
              >
                {collectingTarget && acquisitionKey(collectingTarget) === acquisitionKey(item)
                  ? "采集中…"
                  : `${item.acquisition.label ?? item.acquisition.id} Collect`}
              </button>
              <details className="mt-1 text-xs text-text-dim">
                <summary>{item.acquisition.label ?? item.acquisition.id} 说明</summary>
                {item.acquisition.description && <p>{item.acquisition.description}</p>}
                <p>
                  {item.acquisition.results.map((result) => result.label ?? result.id).join(" · ")}
                </p>
              </details>
              {collectResults[acquisitionKey(item)]?.status &&
                collectResults[acquisitionKey(item)]?.status !== "collected" && (
                  <p role="status" className="text-xs text-yellow">
                    {instrumentReceiptLabel(collectResults[acquisitionKey(item)]?.status ?? "")}
                  </p>
                )}
            </div>
          ))}
        </div>
        {pending.length >= 20 && <p role="status">请先保存待保存结果，再继续采集。</p>}
        <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_11rem] lg:grid-cols-[minmax(0,1fr)_15rem]">
          <div className="min-w-0">
            {current ? (
              <CurrentMeasurement result={current} staged={staged} sessionId={sessionId} />
            ) : (
              <p className="py-16 text-center text-sm text-text-dim">采集后在这里查看曲线</p>
            )}
            {current && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {canSave && (
                  <button className={primaryButton} disabled={saveDisabled} onClick={openSave}>
                    保存所选结果
                  </button>
                )}
                {commandId && current.savedRecordId && (
                  <>
                    <span role="status">已保存</span>
                    <SavedMeasurementLink recordId={current.savedRecordId} />
                  </>
                )}
              </div>
            )}
          </div>
          <aside aria-label="近期结果" className="order-first min-w-0 md:order-last">
            <h4 className="mb-2 text-xs font-semibold">近期结果 · {recent.length}</h4>
            <div className="flex gap-2 overflow-x-auto pb-1 md:grid md:max-h-[28rem] md:overflow-x-hidden md:overflow-y-auto md:pb-0">
              {recent
                .map((_, index) => recent[recent.length - index - 1]!)
                .map((item, index) => (
                  <button
                    key={item.selection.command_id}
                    data-testid={`recent-result-${item.selection.command_id}`}
                    aria-pressed={commandId === item.selection.command_id}
                    title={
                      item.selection.title ||
                      item.target.acquisition.label ||
                      item.target.acquisition.id
                    }
                    onClick={() => onSelect(item)}
                    className={classes(
                      secondaryButton,
                      "h-auto w-36 min-w-0 shrink-0 flex-col items-start whitespace-normal text-left md:w-auto",
                      commandId === item.selection.command_id && "border-blue",
                    )}
                  >
                    <strong className="line-clamp-2 break-words">
                      {recent.length - index}.{" "}
                      {item.selection.title ||
                        item.target.acquisition.label ||
                        item.target.acquisition.id}
                    </strong>
                    <span className="text-xs">
                      {new Date(item.receivedAt).toLocaleTimeString()} ·{" "}
                      {item.savedRecordId
                        ? "已保存"
                        : pending.some(
                              (entry) => entry.selection.command_id === item.selection.command_id,
                            )
                          ? "待保存"
                          : "未保存"}
                    </span>
                  </button>
                ))}
            </div>
          </aside>
        </div>
      </div>
      <Dialog.Root
        open={!!target}
        onOpenChange={(open) => {
          if (!open && !busy) setTarget(undefined);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className={dialogBackdrop} />
          <Dialog.Viewport className={dialogViewport}>
            <Dialog.Popup className={classes(dialogPopup, "p-4")}>
              <Dialog.Title className={dialogTitle}>保存测量</Dialog.Title>
              <Dialog.Description className="text-xs text-text-dim">
                {target?.target.acquisition.label ?? target?.target.acquisition.id} ·{" "}
                {target && new Date(target.receivedAt).toLocaleString()}
              </Dialog.Description>
              <div className="my-3 grid gap-3">
                <label className={fieldLabel}>
                  标题
                  <input
                    className={formControl}
                    value={title}
                    maxLength={500}
                    disabled={busy || !!frozen}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                </label>
                <label className={fieldLabel}>
                  线路名称
                  <input
                    className={formControl}
                    value={line}
                    maxLength={500}
                    disabled={busy || !!frozen}
                    onChange={(event) => setLine(event.target.value)}
                  />
                </label>
                <label className={fieldLabel}>
                  备注
                  <textarea
                    className={formControl}
                    value={note}
                    maxLength={4000}
                    disabled={busy || !!frozen}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </label>
              </div>
              {error && (
                <p role="alert" className="mb-3 text-xs text-yellow">
                  {error}
                </p>
              )}
              <div className="flex justify-end gap-2">
                <button
                  className={secondaryButton}
                  disabled={busy}
                  onClick={() => setTarget(undefined)}
                >
                  取消
                </button>
                <button className={primaryButton} disabled={busy} onClick={() => void save()}>
                  {busy ? "保存中…" : frozen ? "重试保存" : "保存"}
                </button>
              </div>
            </Dialog.Popup>
          </Dialog.Viewport>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
