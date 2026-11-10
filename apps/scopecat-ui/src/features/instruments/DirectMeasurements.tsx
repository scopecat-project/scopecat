import { useState, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import {
  listDirectMeasurements,
  saveDirectMeasurement,
  type MeasurementSave,
  type SavedMeasurement,
} from "./direct-measurement-api";
export type { MeasurementSave } from "./direct-measurement-api";
import type {
  InstrumentAcquisition,
  InstrumentCollectReceipt,
  InstrumentInterface,
} from "../../api-contract";
import {
  COMPARISON_LIMIT,
  MeasurementComparison,
  measurementGroupKey,
  measurementReceiptQuery,
} from "./MeasurementComparison";
import { AcquisitionCurve, CollectReceiptDetails } from "./AcquisitionCurve";
import { interfaceMountPaths } from "./InstrumentInterfaceControls";
import { createInstrumentCommandId } from "./instrument-api";
import { secondaryButton } from "../../ui/styles";

export interface PendingMeasurement {
  saveAttempted?: boolean;
  selection: MeasurementSave;
  acquisition: InstrumentAcquisition;
  receipt: InstrumentCollectReceipt;
}

export function DirectMeasurements({
  pending,
  onSaved,
  instrumentId,
  onResume,
}: {
  instrumentId: string;
  onResume: (selection: MeasurementSave) => void;
  pending: PendingMeasurement[];
  onSaved: (commandId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [cursor, setCursor] = useState<number>();
  const records = useQuery({
    queryKey: ["direct-measurements", cursor],
    queryFn: () => listDirectMeasurements(cursor),
  });
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Record<string, SavedMeasurement>>({});
  const save = useMutation({
    mutationFn: saveDirectMeasurement,
    onSuccess: (record) => {
      onSaved(record.selection.command_id);
      void queryClient.invalidateQueries({ queryKey: ["direct-measurements"] });
    },
  });
  const visibleGroups = new Map<string, SavedMeasurement[]>();
  for (const record of records.data?.items ?? []) {
    const text = [
      record.id,
      record.selection.instrument_id,
      record.description.label,
      record.selection.series_id,
      record.selection.line_id,
      record.selection.line_label,
      record.selection.note,
      record.intent.interface_id,
      record.intent.acquisition_id,
      ...(record.intent.component_path ?? []),
    ]
      .join(" ")
      .toLocaleLowerCase();
    if (!text.includes(search.trim().toLocaleLowerCase())) continue;
    const key = measurementGroupKey(record);
    visibleGroups.set(key, [...(visibleGroups.get(key) ?? []), record]);
  }
  const selectedRecords = Object.values(selected);
  return (
    <section className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3 rounded border border-line p-3 [overflow-wrap:anywhere]">
      <h3 className="text-sm font-semibold">直接测量记录</h3>
      <p className="text-xs text-text-dim">
        保存只保留已返回的完整回执，不会重新采集。未保存结果仅在当前控制会话内可保存，请在结束控制前保存。历史记录的线路归属不会随当前线路修改。
      </p>
      {pending.map((item) => (
        <div key={item.selection.command_id} className="border-b border-line pb-2">
          <span className="text-xs">
            待保存：{item.selection.line_label || "未标注测量对象"} ·{" "}
            {item.acquisition.label ?? item.acquisition.id} · {item.selection.note}
          </span>
          <button
            type="button"
            className={secondaryButton}
            disabled={save.isPending}
            onClick={() => save.mutate(item.selection)}
          >
            保存此结果
          </button>
        </div>
      ))}
      {save.error && <p role="alert">保存失败：{save.error.message}。可重试保存，不要重新采集。</p>}
      {records.error && <p role="alert">无法读取历史记录：{records.error.message}</p>}
      <label className="text-xs">
        查找当前页记录{" "}
        <input
          aria-label="查找当前页记录"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="设备、系列、线路、备注或记录 ID"
        />
      </label>
      <p className="text-xs text-text-dim">
        只搜索当前已加载页；可翻到更早记录继续查找。搜索和翻页不会移除已选记录。按设备 / 系列 / 线路
        ID 分组，同名对象不会合并。
      </p>
      {records.isPending && <p role="status">正在读取记录…</p>}
      {records.data && !visibleGroups.size && <p className="text-xs">当前页没有匹配记录。</p>}
      {[...visibleGroups].map(([key, group]) => (
        <fieldset key={key} className="grid min-w-0 gap-2 border border-line p-2">
          <legend className="text-xs break-all">
            设备 {group[0]!.selection.instrument_id} / 系列{" "}
            {group[0]!.selection.series_id || "未知"} / 线路 {group[0]!.selection.line_id || "未知"}
          </legend>
          {group.map((record) => (
            <div className="flex flex-wrap gap-2 text-xs" key={record.id}>
              <label className="flex gap-2">
                <input
                  type="checkbox"
                  aria-label={`选择记录 ${record.id}`}
                  checked={!!selected[record.id]}
                  disabled={!selected[record.id] && selectedRecords.length >= COMPARISON_LIMIT}
                  onChange={(event) => {
                    const checked = event.target.checked;
                    setSelected((current) => {
                      const next = { ...current };
                      if (checked && Object.keys(current).length < COMPARISON_LIMIT)
                        next[record.id] = record;
                      else if (!checked) delete next[record.id];
                      return next;
                    });
                  }}
                />
                {record.selection.line_label || "未标注测量对象"} ·{" "}
                {record.description.label ?? record.selection.instrument_id} ·{" "}
                {new Date(record.collected_at).toLocaleString()} · {record.selection.note} ·{" "}
                {record.id}
              </label>
              {record.selection.line_id && record.selection.instrument_id === instrumentId && (
                <button
                  type="button"
                  className={secondaryButton}
                  onClick={() => onResume(record.selection)}
                >
                  继续此线路（需确认接线）
                </button>
              )}
            </div>
          ))}
        </fieldset>
      ))}
      <div className="flex gap-2">
        {cursor !== undefined && (
          <button type="button" className={secondaryButton} onClick={() => setCursor(undefined)}>
            最近记录
          </button>
        )}
        {records.data?.next_cursor != null && (
          <button
            type="button"
            className={secondaryButton}
            onClick={() => setCursor(records.data!.next_cursor!)}
          >
            更早记录
          </button>
        )}
      </div>
      <p className="text-xs text-text-dim">
        已选 {selectedRecords.length} / {COMPARISON_LIMIT} 条，最多比较 {COMPARISON_LIMIT}{" "}
        条记录；每条曲线保留自己的坐标与单位。
      </p>
      <div className="flex flex-wrap gap-2">
        {selectedRecords.map((record) => (
          <button
            key={record.id}
            type="button"
            className={secondaryButton}
            onClick={() =>
              setSelected((current) => {
                const next = { ...current };
                delete next[record.id];
                return next;
              })
            }
          >
            移除已选记录 {record.id}
          </button>
        ))}
      </div>
      {selectedRecords.length > 1 && (
        <MeasurementComparison
          entries={selectedRecords.map((record) => ({
            record,
            acquisition: recordedAcquisition(record),
          }))}
        />
      )}
      {selectedRecords.map((record) => (
        <SavedCurve key={record.id} record={record} />
      ))}
    </section>
  );
}

export function SavedCurve({
  record,
  readOnly = false,
}: {
  record: SavedMeasurement;
  readOnly?: boolean;
}) {
  const cache = useQueryClient();
  const detail = useQuery({
    queryKey: ["direct-measurement-detail", record.id],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/direct-measurements/{record_id}", {
          params: { path: { record_id: record.id } },
        }),
      ),
  });
  const [note, setNote] = useState("");
  const [anomalous, setAnomalous] = useState(false);
  const noteOperation = useRef<string | undefined>(undefined);
  const annotate = useMutation({
    mutationFn: () => {
      noteOperation.current ??= createInstrumentCommandId("note");
      return apiData(
        apiClient.POST("/api/v1/direct-measurements/{record_id}/notes", {
          params: { path: { record_id: record.id } },
          body: { operation_id: noteOperation.current, note, anomalous },
        }),
      );
    },
    onSuccess: () => {
      noteOperation.current = undefined;
      setNote("");
      void cache.invalidateQueries({ queryKey: ["direct-measurements"] });
    },
  });

  const receipt = useQuery(measurementReceiptQuery(record));
  const acquisition = recordedAcquisition(record);
  return (
    <section className="min-w-0 border-t border-line pt-2">
      <h4>
        {record.selection.title || record.selection.line_label || "未命名测量"} ·{" "}
        {record.selection.note}
      </h4>
      <p className="text-xs [overflow-wrap:anywhere]">
        {record.id} · {new Date(record.collected_at).toLocaleString()}
      </p>
      <div className="grid gap-2 text-xs">
        {(annotate.data?.notes ?? detail.data?.notes ?? []).map((entry) => (
          <p key={entry.operation_id}>
            {entry.anomalous ? "异常" : "备注"} · {new Date(entry.recorded_at).toLocaleString()} ·{" "}
            {entry.note}
          </p>
        ))}
        {!readOnly && (
          <>
            <label>
              追加备注{" "}
              <input
                value={note}
                disabled={annotate.isPending}
                onChange={(event) => {
                  noteOperation.current = undefined;
                  setNote(event.target.value);
                }}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={anomalous}
                disabled={annotate.isPending}
                onChange={(event) => {
                  noteOperation.current = undefined;
                  setAnomalous(event.target.checked);
                }}
              />
              标记异常
            </label>
            <button
              type="button"
              className={secondaryButton}
              disabled={annotate.isPending}
              onClick={() => annotate.mutate()}
            >
              追加标记（不修改原结果）
            </button>
          </>
        )}
        {annotate.error && <p role="alert">{annotate.error.message}</p>}
      </div>
      {receipt.error && <p role="alert">{receipt.error.message}</p>}
      {receipt.data &&
        (acquisition ? (
          <AcquisitionCurve receipt={receipt.data} acquisition={acquisition} />
        ) : (
          <>
            <p role="alert">无法解析保存的采集契约或组件路径；完整回执仍可查看。</p>
            <CollectReceiptDetails receipt={receipt.data} />
          </>
        ))}
      <details>
        <summary>设备、线路和设置来源</summary>
        <p className="text-xs">
          采集前观测不保证与曲线生成时设置一致；已有曲线的生成证据以回执为准。未知信息保持未知。
        </p>
        <pre className="max-h-72 overflow-auto text-xs">{JSON.stringify(record, null, 2)}</pre>
      </details>
    </section>
  );
}

export function recordedAcquisition(record: SavedMeasurement): InstrumentAcquisition | undefined {
  const { interface_id, component_path = [], acquisition_id } = record.intent;
  const contract = record.description.interfaces?.find((item) => item.id === interface_id);
  if (!contract) return undefined;
  // Match the backend's longest-mount-first resolution, then walk the path
  // relative to that mount inside the retained interface declaration.
  for (const mount of interfaceMountPaths(record.description, interface_id).sort(
    (a, b) => b.length - a.length,
  )) {
    if (!mount.every((segment, index) => component_path[index] === segment)) continue;
    let member: Pick<InstrumentInterface, "components" | "acquisitions"> | undefined = contract;
    for (const segment of component_path.slice(mount.length)) {
      member = member?.components?.find((component) => component.id === segment);
    }
    if (member) return member.acquisitions?.find((item) => item.id === acquisition_id);
  }
  return undefined;
}
