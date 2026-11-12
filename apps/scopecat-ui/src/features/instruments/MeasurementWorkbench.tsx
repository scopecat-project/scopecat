import { ReceiptValidationNotice } from "./ReceiptValidation";
import type { MeasurementDisplayStore } from "./BoundedMeasurement";
import { measurementKey, sameMeasurement } from "./measurement-identity";
import { SavedMeasurementLink } from "./SavedMeasurementView";
import { useEffect, useRef, useState } from "react";
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
  layoutManaged = false,
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
  layoutManaged?: boolean;
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
  onSaved: (commandId: string, recordId?: string, selection?: MeasurementSave) => void;
}) {
  const host = useRef<HTMLElement>(null);
  const saveButton = useRef<HTMLButtonElement>(null);
  const actions = useRef<HTMLDivElement>(null);
  const hasCurrent = !!current;
  useEffect(() => {
    if (!layoutManaged || !hasCurrent) return;
    const strip = actions.current;
    const pane = host.current?.closest<HTMLElement>(".device-pane-scroll");
    if (!strip || !pane) return;
    const previousPadding = pane.style.scrollPaddingTop;
    const measure = () => {
      const height = strip.getBoundingClientRect().height;
      // Hidden panes report zero; keep the last visible inset until shown again.
      if (height > 0) pane.style.scrollPaddingTop = `${height + 8}px`;
    };
    measure();
    const observer =
      typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(measure);
    observer?.observe(strip);
    if (!observer) window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      if (!observer) window.removeEventListener("resize", measure);
      pane.style.scrollPaddingTop = previousPadding;
    };
  }, [layoutManaged, hasCurrent]);
  const cache = useQueryClient();
  const [displayStore] = useState<MeasurementDisplayStore>(() => new Map());
  const [target, setTarget] = useState<CurrentMeasurementResult>();
  const drafts = useRef(new Map<string, { title: string; line: string; note: string }>());
  useEffect(() => {
    const alive = new Set([
      ...recent.map((item) => measurementKey(item.selection)),
      ...pending.map((item) => measurementKey(item.selection)),
      ...(current ? [measurementKey(current.selection)] : []),
    ]);
    for (const key of drafts.current.keys()) if (!alive.has(key)) drafts.current.delete(key);
    for (const key of displayStore.keys())
      if (!alive.has(key) && !drafts.current.has(key)) displayStore.delete(key);
  }, [recent, pending, current, displayStore]);
  const [title, setTitle] = useState("");
  const [line, setLine] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const [error, setError] = useState("");
  const targets = acquisitionTargets(description);
  if (!targets.length && !current) return null;
  const commandId = current?.selection.command_id;
  const canSave = pending.some(
    (item) => !!current && sameMeasurement(item.selection, current.selection),
  );
  const pendingTarget = pending.find(
    (item) => !!target && sameMeasurement(item.selection, target.selection),
  );
  const frozen = pendingTarget?.saveAttempted ? pendingTarget.selection : undefined;
  const openSave = () => {
    if (!current || !canSave || saveDisabled) return;
    const selection =
      pending.find((item) => sameMeasurement(item.selection, current.selection))?.selection ??
      current.selection;
    setTarget(current);
    const draft = drafts.current.get(measurementKey(selection));
    setTitle(draft?.title ?? selection.title ?? "");
    setLine(draft?.line ?? selection.line_label ?? "");
    setNote(draft?.note ?? selection.note ?? "");
    setError("");
  };
  const editDraft = (change: Partial<{ title: string; line: string; note: string }>) => {
    if (!target) return;
    const value = { title, line, note, ...change };
    drafts.current.set(measurementKey(target.selection), value);
    setTitle(value.title);
    setLine(value.line);
    setNote(value.note);
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
      onSaved(id, record.id, selection);
      drafts.current.delete(measurementKey(target.selection));
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
      ref={host}
      aria-label="交互采集"
      className={classes(
        "min-w-0",
        collapsed ? "justify-self-start" : "rounded-md border border-line bg-panel-soft p-3",
      )}
    >
      {!layoutManaged && (
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
      )}
      <div hidden={collapsed}>
        {!layoutManaged && (
          <MeasurementAcquisitionActions
            description={description}
            connected={connected}
            disabled={disabled}
            collectingTarget={collectingTarget}
            collectResults={collectResults}
            pendingCount={pending.length}
            onCollect={onCollect}
          />
        )}
        <div className="grid min-w-0 gap-3">
          {current && (
            <div
              ref={actions}
              role="group"
              aria-label="所选结果操作"
              className={classes(
                "flex flex-wrap items-center gap-2",
                // This shares the full result/list containing block, so it stays
                // available even after scrolling past the current preview.
                layoutManaged && "sticky top-0 z-10 bg-panel-soft py-2",
              )}
            >
              {canSave && (
                <button
                  ref={saveButton}
                  className={primaryButton}
                  disabled={saveDisabled}
                  onClick={openSave}
                >
                  保存所选结果
                </button>
              )}
              {recent.length > 0 && (
                <label className="flex min-w-0 max-w-full flex-1 basis-48 items-center gap-2 text-xs">
                  <span className="shrink-0">近期结果</span>
                  <select
                    aria-label="选择近期结果"
                    className={classes(formControl, "w-full min-w-0 max-w-64")}
                    value={measurementKey(current.selection)}
                    onChange={(event) => {
                      const result = recent.find(
                        (item) => measurementKey(item.selection) === event.target.value,
                      );
                      if (result) onSelect(result);
                    }}
                  >
                    {!recent.some((item) => sameMeasurement(item.selection, current.selection)) && (
                      <option value={measurementKey(current.selection)} disabled>
                        当前显示（近期列表外） ·{" "}
                        {current.selection.title ||
                          current.target.acquisition.label ||
                          current.target.acquisition.id}
                      </option>
                    )}
                    {recent
                      .map((_, index) => recent[recent.length - index - 1]!)
                      .map((item, index) => (
                        <option
                          key={measurementKey(item.selection)}
                          value={measurementKey(item.selection)}
                        >
                          {recent.length - index}.{" "}
                          {item.selection.title ||
                            item.target.acquisition.label ||
                            item.target.acquisition.id}
                          {" · "}
                          {item.savedRecordId
                            ? "已保存"
                            : pending.some((entry) =>
                                  sameMeasurement(entry.selection, item.selection),
                                )
                              ? "待保存"
                              : "未保存"}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              {commandId && current.savedRecordId && (
                <>
                  <span role="status">已保存</span>
                  <SavedMeasurementLink recordId={current.savedRecordId} />
                </>
              )}
            </div>
          )}
          <div className="min-w-0">
            {current ? (
              <CurrentMeasurement
                result={current}
                staged={staged}
                sessionId={sessionId}
                displayStore={displayStore}
              />
            ) : (
              <p className="py-16 text-center text-sm text-text-dim">采集后在这里查看曲线</p>
            )}
          </div>
          <aside aria-label="近期结果" className="min-w-0">
            <h4 className="mb-2 text-xs font-semibold">近期结果 · {recent.length}</h4>
            <div className="grid gap-2">
              {recent
                .map((_, index) => recent[recent.length - index - 1]!)
                .map((item, index) => (
                  <button
                    key={measurementKey(item.selection)}
                    data-testid={`recent-result-${item.selection.command_id}`}
                    aria-pressed={!!current && sameMeasurement(current.selection, item.selection)}
                    title={
                      item.selection.title ||
                      item.target.acquisition.label ||
                      item.target.acquisition.id
                    }
                    onClick={() => onSelect(item)}
                    className={classes(
                      secondaryButton,
                      "h-auto w-full min-w-0 flex-col items-start whitespace-normal text-left",
                      !!current &&
                        sameMeasurement(current.selection, item.selection) &&
                        "border-blue",
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
                        : pending.some((entry) => sameMeasurement(entry.selection, item.selection))
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
            <Dialog.Popup
              className={classes(dialogPopup, "p-4")}
              finalFocus={() => {
                if (!layoutManaged) return true;
                if (!host.current?.closest("[hidden]"))
                  return (
                    saveButton.current ??
                    host.current?.closest<HTMLElement>(".device-pane-scroll") ??
                    true
                  );
                // Resizing can restore a wide control-only layout while the portal is open.
                // Return focus to its visible results entry, without opening results or losing drafts.
                return (
                  host.current
                    .closest(".device-control-layout")
                    ?.querySelector<HTMLButtonElement>('[data-pane="results"]') ?? true
                );
              }}
            >
              <Dialog.Title className={dialogTitle}>保存测量</Dialog.Title>
              <Dialog.Description className="text-xs text-text-dim">
                {target?.target.acquisition.label ?? target?.target.acquisition.id} ·{" "}
                {target && new Date(target.receivedAt).toLocaleString()}
              </Dialog.Description>
              {target && <ReceiptValidationNotice value={target} />}
              <div className="my-3 grid gap-3">
                <label className={fieldLabel}>
                  标题
                  <input
                    className={formControl}
                    value={title}
                    maxLength={500}
                    disabled={busy || !!frozen}
                    onChange={(event) => editDraft({ title: event.target.value })}
                  />
                </label>
                <label className={fieldLabel}>
                  线路名称
                  <input
                    className={formControl}
                    value={line}
                    maxLength={500}
                    disabled={busy || !!frozen}
                    onChange={(event) => editDraft({ line: event.target.value })}
                  />
                </label>
                <label className={fieldLabel}>
                  备注
                  <textarea
                    className={formControl}
                    value={note}
                    maxLength={4000}
                    disabled={busy || !!frozen}
                    onChange={(event) => editDraft({ note: event.target.value })}
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

/** Acquisition stays in the control pane, independent of result visibility. */
export function MeasurementAcquisitionActions({
  description,
  connected,
  disabled,
  collectingTarget,
  collectResults,
  pendingCount,
  onCollect,
}: {
  description?: InstrumentDescription;
  connected: boolean;
  disabled: boolean;
  collectingTarget?: InstrumentAcquisitionTarget;
  collectResults: Record<string, InstrumentCollectReceipt>;
  pendingCount: number;
  onCollect: (target: InstrumentAcquisitionTarget) => void;
}) {
  const targets = acquisitionTargets(description);
  if (!targets.length) return null;
  return (
    <section aria-label="采集操作">
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
      {pendingCount >= 20 && <p role="status">请先保存待保存结果，再继续采集。</p>}
    </section>
  );
}
