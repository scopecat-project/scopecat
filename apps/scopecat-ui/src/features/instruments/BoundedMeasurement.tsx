import { secondaryButton, formControl } from "../../ui/styles";
import { useState } from "react";
import type { InstrumentAcquisition } from "../../api-contract";
import { unavailableReasonLabels } from "./InstrumentInterfaceControls";
import { EChart } from "../../ui/EChart";
import type { ChartInteraction } from "../../ui/chart-interaction";
import {
  previewSourceKey,
  useMeasurementPreview,
  type PreviewSource,
  type PreviewField,
} from "./measurement-preview";
export type Representation = "magnitude" | "phase" | "real" | "imag";
export interface MeasurementDisplayState {
  start: number;
  previousStarts?: number[];
  representation: Representation;
  field?: string;
  dataOpen?: boolean;
  zoom: Map<string, ChartInteraction>;
}
export type MeasurementDisplayStore = Map<string, MeasurementDisplayState>;
/** Tick labels only: the series, tooltip and exact-value table keep original units/values. */
export function formatPreviewTick(value: number) {
  const magnitude = Math.abs(value);
  return Number.isFinite(value) && (magnitude >= 1e6 || (magnitude > 0 && magnitude < 0.001))
    ? value.toExponential()
    : String(value);
}
export function previewNumbers(
  field: PreviewField,
  representation: Representation,
): (number | null)[] {
  return field.values.map((value) => {
    if (field.dtype === "int64") {
      if (typeof value !== "string") return null;
      const n = Number(value);
      return Number.isSafeInteger(n) ? n : null;
    }
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      "real" in value &&
      "imag" in value &&
      typeof value.real === "number" &&
      typeof value.imag === "number"
    ) {
      const n =
        representation === "real"
          ? value.real
          : representation === "imag"
            ? value.imag
            : representation === "phase"
              ? Math.atan2(value.imag, value.real)
              : Math.hypot(value.real, value.imag);
      return Number.isFinite(n) ? n : null;
    }
    return null;
  });
}
const valid = (value: PreviewField) =>
  value.kind === "array" &&
  value.shape.length === 1 &&
  value.total_count !== null &&
  value.shape[0] === value.total_count &&
  value.start >= 0 &&
  value.returned_count === value.values.length &&
  value.start + value.returned_count <= value.total_count &&
  !value.unit_truncated;

export function alignedPreviewFields(
  field: PreviewField,
  coordinate: PreviewField | undefined,
  result: InstrumentAcquisition["results"][number],
  coordinateContract?: InstrumentAcquisition["results"][number],
) {
  if (
    !valid(field) ||
    field.dtype !== result.dtype ||
    (result.unit != null && field.unit !== result.unit)
  )
    return false;
  const axis = result.axes?.length === 1 ? result.axes[0] : undefined;
  if (!axis?.coordinate_result) return (result.axes?.length ?? 0) <= 1;
  return (
    !!coordinate &&
    !!coordinateContract &&
    valid(coordinate) &&
    coordinateContract.role === "coordinate" &&
    coordinate.dtype !== "complex128" &&
    coordinate.dtype !== "string" &&
    coordinate.dtype !== "bool" &&
    coordinate.dtype === coordinateContract.dtype &&
    (coordinateContract.unit == null || coordinate.unit === coordinateContract.unit) &&
    (axis.unit == null || coordinate.unit === axis.unit) &&
    coordinate.total_count === field.total_count &&
    coordinate.start === field.start &&
    coordinate.returned_count === field.returned_count
  );
}
export function BoundedMeasurement({
  source,
  acquisition,
  displayStore,
  displayKey,
}: {
  source: PreviewSource;
  acquisition?: InstrumentAcquisition;
  displayStore?: MeasurementDisplayStore;
  displayKey?: string;
}) {
  const [local] = useState<MeasurementDisplayStore>(() => new Map());
  const store = displayStore ?? local;
  const identity = displayKey ?? previewSourceKey(source);
  if (!store.has(identity))
    store.set(identity, { start: 0, representation: "magnitude", zoom: new Map() });
  const state = store.get(identity)!;
  const [, rerender] = useState(0);
  const update = (change: Partial<MeasurementDisplayState>) => {
    Object.assign(state, change);
    rerender((n) => n + 1);
  };
  const fieldContract = acquisition?.results.find((field) => field.id === state.field);
  const requestIds = state.field
    ? [
        ...new Set([
          state.field,
          ...(fieldContract?.axes ?? []).flatMap((axis) =>
            axis.coordinate_result ? [axis.coordinate_result] : [],
          ),
        ]),
      ].slice(0, 16)
    : undefined;
  const view = useMeasurementPreview(source, state.start, requestIds);
  const data = view.data;
  const fields = data?.fields ?? [];
  const unavailable = fields.filter((field) => field.kind === "unavailable");
  const reasons = [...new Set(unavailable.map((field) => field.reason ?? "未知"))]
    .sort()
    .map(
      (reason) => unavailableReasonLabels[reason as keyof typeof unavailableReasonLabels] ?? reason,
    );
  const selected = fields.find((field) => field.request_id === state.field) ?? fields[0];
  const next = fields
    .map((field) => field.next_start)
    .filter((value): value is number => value != null);
  return (
    <section className="grid min-w-0 gap-2" aria-label="有界结果预览">
      <p className="text-xs">
        连续原始数据窗口，不是全局概览或降采样。翻页查看其他范围；图内缩放仅作用于本窗口，切换窗口后重新缩放。保存始终使用完整原始数据。
      </p>
      <label>
        复数表示{" "}
        <select
          aria-label="复数表示"
          value={state.representation}
          onChange={(event) => update({ representation: event.target.value as Representation })}
        >
          <option value="magnitude">幅值</option>
          <option value="phase">相位 (rad)</option>
          <option value="real">实部</option>
          <option value="imag">虚部</option>
        </select>
      </label>
      {view.isPending && <p role="status">正在读取局部结果…</p>}
      {view.error && (
        <p role="alert">
          {view.error.message}
          <button onClick={view.retry}>重试预览</button>
        </p>
      )}
      {unavailable.length > 0 && (
        <p role="status">
          {unavailable.length} 项结果不可用 · 原因：{reasons.join("、")}
        </p>
      )}
      {data && (
        <>
          {(data.fields_truncated || data.metadata_truncated || data.problems_truncated) && (
            <p>部分字段或证据未展开；这里不是完整原始回执。</p>
          )}
          {fields.map((field) => {
            const result = acquisition?.results.find((item) => item.id === field.request_id);
            if (!result || result.role === "coordinate") return null;
            const coordinateId =
              result.axes?.length === 1 ? result.axes[0]?.coordinate_result : undefined;
            const coordinate = fields.find((item) => item.request_id === coordinateId);
            const y = previewNumbers(field, state.representation);
            const x = coordinate ? previewNumbers(coordinate, "real") : undefined;
            const aligned = alignedPreviewFields(
              field,
              coordinate,
              result,
              acquisition?.results.find((item) => item.id === coordinateId),
            );
            const chartKey = JSON.stringify([
              identity,
              field.request_id,
              state.start,
              state.representation,
            ]);
            return (
              <section key={field.request_id}>
                <p>
                  {field.request_id} · {field.complete ? "完整" : "局部"}{" "}
                  {field.returned_count ? field.start + 1 : 0}–{field.start + field.returned_count}{" "}
                  / {field.total_count ?? "未知"} · 原始形状 [{field.shape.join(", ")}]
                </p>
                {field.kind === "array" &&
                field.shape.length === 1 &&
                aligned &&
                !field.unit_truncated &&
                !coordinate?.unit_truncated ? (
                  <EChart
                    ariaLabel={`${field.request_id} 局部曲线`}
                    height={260}
                    pointCount={y.length}
                    seriesCount={1}
                    seriesLabels={[field.request_id]}
                    interactionKey={chartKey}
                    interactionStore={{
                      read: (key) => state.zoom.get(key),
                      write: (key, value) => {
                        for (const oldKey of state.zoom.keys()) {
                          const old = JSON.parse(oldKey) as unknown[];
                          if (
                            old[1] === field.request_id &&
                            old[3] === state.representation &&
                            oldKey !== key
                          )
                            state.zoom.delete(oldKey);
                        }
                        state.zoom.set(key, value);
                      },
                    }}
                    option={{
                      animation: false,
                      tooltip: { trigger: "axis" },
                      grid: {
                        left: 10,
                        right: 15,
                        top: 20,
                        bottom: 55,
                        outerBoundsMode: "same",
                        outerBoundsContain: "all",
                      },
                      xAxis: {
                        type: "value",
                        name: coordinateId
                          ? `${coordinateId} (${coordinate?.unit ?? "未知单位"})`
                          : "原始下标",
                        splitNumber: 3,
                        nameTextStyle: { textMargin: 3 },
                        axisLabel: {
                          hideOverlap: true,
                          textMargin: 3,
                          margin: 12,
                          formatter: formatPreviewTick,
                        },
                        scale: true,
                      },
                      yAxis: {
                        type: "value",
                        nameTextStyle: { textMargin: 3 },
                        axisLabel: {
                          hideOverlap: true,
                          textMargin: 3,
                          formatter: formatPreviewTick,
                        },
                        name:
                          field.dtype === "complex128" && state.representation === "phase"
                            ? "rad"
                            : (field.unit ?? "未知单位"),
                        scale: true,
                      },
                      dataZoom: [{ type: "inside" }, { type: "slider" }],
                      series: [
                        {
                          type: "line",
                          showSymbol: false,
                          connectNulls: false,
                          data: y.map((sample, index) => [
                            x ? x[index] : field.start + index,
                            sample,
                          ]),
                        },
                      ],
                    }}
                  />
                ) : (
                  <p>此字段不具备对齐的一维坐标，保留数值窗口，不绘制推测曲线。</p>
                )}
              </section>
            );
          })}
          <div
            role="group"
            aria-label="局部窗口导航"
            className="flex flex-wrap items-center gap-2 text-xs"
          >
            <label>
              起始下标{" "}
              <input
                className={`${formControl} w-28`}
                aria-label="预览起始下标"
                type="number"
                min={0}
                max={Number.MAX_SAFE_INTEGER}
                value={state.start}
                onChange={(event) => {
                  const start = Number(event.target.value);
                  if (Number.isSafeInteger(start) && start >= 0)
                    update({ start, previousStarts: [] });
                }}
              />
            </label>
            <button
              className={secondaryButton}
              disabled={state.start === 0}
              onClick={() =>
                update({
                  start: state.previousStarts?.at(-1) ?? Math.max(0, state.start - 512),
                  previousStarts: state.previousStarts?.slice(0, -1),
                })
              }
            >
              上一页数据
            </button>
            <button
              className={secondaryButton}
              disabled={!next.length}
              onClick={() =>
                update({
                  start: Math.min(...next),
                  previousStarts: [...(state.previousStarts ?? []), state.start],
                })
              }
            >
              下一页数据
            </button>
          </div>
          <details
            open={state.dataOpen ?? false}
            onToggle={(event) => {
              const dataOpen = event.currentTarget.open;
              if (dataOpen !== (state.dataOpen ?? false)) update({ dataOpen });
            }}
          >
            <summary className="cursor-pointer text-xs">查看局部原始数值（按窗口）</summary>
            <div className="mt-2 grid min-w-0 gap-2 text-xs">
              <label>
                结果字段{" "}
                <select
                  aria-label="精确结果字段"
                  value={selected?.request_id ?? ""}
                  onChange={(event) =>
                    update({ field: event.target.value, start: 0, previousStarts: [] })
                  }
                >
                  {[
                    ...new Set([
                      ...(acquisition?.results.map((field) => field.id) ?? []),
                      ...fields.map((field) => field.request_id),
                    ]),
                  ].map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
              </label>
              {selected && (
                <>
                  <p>
                    {selected.dtype} · {selected.unit ?? "未知单位"}
                    {selected.unit_truncated ? "（单位已截断）" : ""} · C-order 原始下标；int64
                    保留十进制原值，超出安全整数范围不绘图。
                  </p>
                  {selected.reason && <p>{selected.reason}</p>}
                  <div role="region" aria-label="局部原始数值表" className="max-h-72 overflow-auto">
                    <table>
                      <thead>
                        <tr>
                          <th>原始下标</th>
                          <th>原始数值</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selected.values.map((value, index) => (
                          <tr key={selected.start + index}>
                            <td>{selected.start + index}</td>
                            <td>
                              {typeof value === "string" ? value : JSON.stringify(value)}
                              {selected.truncated_indices.includes(selected.start + index) &&
                                "（文本已截断）"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          </details>
          <details>
            <summary>有界证据摘要（不是完整回执）</summary>
            <pre className="max-h-72 overflow-auto">
              {JSON.stringify(
                {
                  metadata: data.metadata,
                  problems: data.problems,
                  fields: fields.map(({ values: _values, ...field }) => field),
                },
                null,
                2,
              )}
            </pre>
          </details>
        </>
      )}
    </section>
  );
}
