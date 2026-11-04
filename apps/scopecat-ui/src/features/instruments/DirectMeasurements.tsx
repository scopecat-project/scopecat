import { useState, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData, ApiError } from "../../api-client";
import {
  listDirectMeasurements,
  saveDirectMeasurement,
  type MeasurementSave,
  type SavedMeasurement,
} from "./direct-measurement-api";
export type { MeasurementSave } from "./direct-measurement-api";
import type { InstrumentAcquisition, InstrumentCollectReceipt } from "../../api-contract";
import { decodeCollectReceipt } from "./hardware-receipt-wire";
import { AcquisitionCurve } from "./AcquisitionCurve";
import { createInstrumentCommandId } from "./instrument-api";
import { secondaryButton } from "../../ui/styles";

export interface PendingMeasurement {
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
  const [selected, setSelected] = useState<Record<string, SavedMeasurement>>({});
  const save = useMutation({
    mutationFn: saveDirectMeasurement,
    onSuccess: (record) => {
      onSaved(record.selection.command_id);
      void queryClient.invalidateQueries({ queryKey: ["direct-measurements"] });
    },
  });
  return (
    <section className="grid gap-3 rounded border border-line p-3">
      <h3 className="text-sm font-semibold">直接测量记录</h3>
      <p className="text-xs text-text-dim">
        保存只保留已返回的完整回执，不会重新采集。未保存结果仅在当前控制会话内可保存，请在结束控制前保存。历史记录的线路归属不会随当前线路修改。
      </p>
      {pending.map((item) => (
        <div key={item.selection.command_id} className="border-b border-line pb-2">
          <span className="text-xs">
            待保存：{item.selection.line_label || "对象未知"} ·{" "}
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
      {records.data?.items.map((record) => (
        <label className="flex gap-2 text-xs" key={record.id}>
          <input
            type="checkbox"
            checked={!!selected[record.id]}
            onChange={(event) =>
              setSelected((current) => {
                const next = { ...current };
                if (event.target.checked) next[record.id] = record;
                else delete next[record.id];
                return next;
              })
            }
          />
          {record.selection.line_label || "对象未知"} ·{" "}
          {record.description.label ?? record.selection.instrument_id} ·{" "}
          {new Date(record.collected_at).toLocaleString()} · {record.selection.note}
          {record.selection.line_id && record.selection.instrument_id === instrumentId && (
            <button
              type="button"
              className={secondaryButton}
              onClick={(event) => {
                event.preventDefault();
                onResume(record.selection);
              }}
            >
              继续此线路（需确认接线）
            </button>
          )}
        </label>
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
        勾选记录逐项比较；每条曲线保留自己的坐标与单位，不自动重采样。
      </p>
      {Object.values(selected).map((record) => (
        <SavedCurve key={record.id} record={record} />
      ))}
    </section>
  );
}

function SavedCurve({ record }: { record: SavedMeasurement }) {
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

  const receipt = useQuery({
    queryKey: ["direct-measurement-receipt", record.id],
    queryFn: async () => {
      const response = await fetch(
        `/api/v1/direct-measurements/${encodeURIComponent(record.id)}/receipt`,
      );
      if (!response.ok) throw new ApiError("保存的回执读取失败", response.status);
      return decodeCollectReceipt(await response.arrayBuffer());
    },
  });
  const acquisition = record.description.interfaces
    ?.find((item) => item.id === record.intent.interface_id)
    ?.acquisitions?.find((item) => item.id === record.intent.acquisition_id);
  return (
    <section className="min-w-0 border-t border-line pt-2">
      <h4>
        {record.selection.line_label || "对象未知"} · {record.selection.note}
      </h4>
      <p className="text-xs">
        {record.id} · {new Date(record.collected_at).toLocaleString()}
      </p>
      <div className="grid gap-2 text-xs">
        {(annotate.data?.notes ?? detail.data?.notes ?? []).map((entry) => (
          <p key={entry.operation_id}>
            {entry.anomalous ? "异常" : "备注"} · {new Date(entry.recorded_at).toLocaleString()} ·{" "}
            {entry.note}
          </p>
        ))}
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
        {annotate.error && <p role="alert">{annotate.error.message}</p>}
      </div>
      {receipt.error && <p role="alert">{receipt.error.message}</p>}
      {receipt.data && acquisition && (
        <AcquisitionCurve receipt={receipt.data} acquisition={acquisition} />
      )}
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
