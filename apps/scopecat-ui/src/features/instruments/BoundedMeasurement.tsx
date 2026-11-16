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
  plotField?: string;
  dataStart?: number;
  dataPreviousStarts?: number[];
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
  workspace = false,
}: {
  source: PreviewSource;
  acquisition?: InstrumentAcquisition;
  displayStore?: MeasurementDisplayStore;
  displayKey?: string;
  workspace?: boolean;
}) {
  const [local] = useState<MeasurementDisplayStore>(() => new Map());
  const store = displayStore ?? local;
  const identity = displayKey ?? previewSourceKey(source);
  if (!store.has(identity))
    store.set(identity, { start: 0, representation: "magnitude", zoom: new Map() });
  const state = store.get(identity)!;
  const [, rerender] = useState(0);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const update = (change: Partial<MeasurementDisplayState>) => {
    Object.assign(state, change);
    rerender((n) => n + 1);
  };
  const plotFields = acquisition?.results.filter((field) => field.role !== "coordinate") ?? [];
  const plotId = workspace
    ? (plotFields.find((field) => field.id === state.plotField)?.id ?? plotFields[0]?.id)
    : undefined;
  const requestedFields = workspace
    ? [detailsOpen ? (state.field ?? plotId) : plotId].filter((id): id is string => !!id)
    : state.field
      ? [state.field]
      : [];
  const requestIds = requestedFields.length
    ? [
        ...new Set(
          requestedFields.flatMap((id) => [
            id,
            ...(acquisition?.results.find((field) => field.id === id)?.axes ?? []).flatMap((axis) =>
              axis.coordinate_result ? [axis.coordinate_result] : [],
            ),
          ]),
        ),
      ].slice(0, 16)
    : undefined;
  const exactView = workspace && detailsOpen;
  const activeStart = exactView ? (state.dataStart ?? 0) : state.start;
  const previousStarts = exactView ? state.dataPreviousStarts : state.previousStarts;
  const setWindow = (start: number, history: number[] = []) =>
    update(
      exactView
        ? { dataStart: start, dataPreviousStarts: history }
        : { start, previousStarts: history },
    );
  const view = useMeasurementPreview(source, activeStart, requestIds);
  const data = view.data;
  const fields = data?.fields ?? [];
  const unavailable = fields.filter((field) => field.kind === "unavailable");
  const reasons = [...new Set(unavailable.map((field) => field.reason ?? "未知"))]
    .sort()
    .map(
      (reason) => unavailableReasonLabels[reason as keyof typeof unavailableReasonLabels] ?? reason,
    );
  const selected =
    fields.find((field) => field.request_id === (state.field ?? plotId)) ?? fields[0];
  const next = fields
    .map((field) => field.next_start)
    .filter((value): value is number => value != null);
  return (
    <section
      className="min-w-0 max-w-full gap-2 [overflow-wrap:anywhere]"
      style={
        workspace
          ? {
              position: "relative",
              display: "flex",
              flexDirection: "column",
              flex: 1,
              minHeight: 0,
              overflow: "hidden",
            }
          : { display: "grid", gridTemplateColumns: "minmax(0, 1fr)" }
      }
      aria-label="有界结果预览"
    >
      {!workspace && (
        <p className="text-xs">
          连续原始数据窗口，不是全局概览或降采样。翻页查看其他范围；图内缩放仅作用于本窗口，切换窗口后重新缩放。保存始终使用完整原始数据。
        </p>
      )}
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
        {workspace && plotFields.length > 1 && (
          <label>
            <span className="sr-only">曲线字段</span>
            <select
              aria-label="曲线字段"
              className={formControl}
              style={{ width: "auto", maxWidth: "9rem" }}
              value={plotId ?? ""}
              onChange={(event) =>
                update({ plotField: event.target.value, start: 0, previousStarts: [] })
              }
            >
              {plotFields.map((field) => (
                <option key={field.id} value={field.id}>
                  {field.label ?? field.id}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          <span className={workspace ? "sr-only" : undefined}>复数表示</span>{" "}
          <select
            aria-label="复数表示"
            className={formControl}
            value={state.representation}
            onChange={(event) => update({ representation: event.target.value as Representation })}
          >
            <option value="magnitude">幅值</option>
            <option value="phase">相位 (rad)</option>
            <option value="real">实部</option>
            <option value="imag">虚部</option>
          </select>
        </label>
        {workspace && (
          <button
            className={secondaryButton}
            aria-expanded={detailsOpen}
            disabled={!data && !detailsOpen}
            onClick={() => setDetailsOpen(!detailsOpen)}
          >
            {detailsOpen ? "关闭数据详情" : "数据详情"}
          </button>
        )}
      </div>
      {view.isPending && <p role="status">正在读取局部结果…</p>}
      {view.error && (
        <p role="alert">
          {view.error.message}
          <button className={secondaryButton} onClick={view.retry}>
            重试预览
          </button>
        </p>
      )}
      {unavailable.length > 0 && (
        <p role="status">
          {unavailable.length} 项结果不可用 · 原因：{reasons.join("、")}
        </p>
      )}
      {data && (
        <div
          style={
            workspace
              ? {
                  flex: 1,
                  minHeight: 0,
                  display: "flex",
                  flexDirection: "column",
                  overflow: "hidden",
                }
              : undefined
          }
        >
          <div
            hidden={workspace && detailsOpen}
            style={
              workspace && !detailsOpen
                ? {
                    flex: 1,
                    minHeight: 0,
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    overflow: "hidden",
                  }
                : undefined
            }
          >
            {workspace && !plotId && (
              <p role="status">没有可绘制的字段契约，请打开数据详情查看原始数值。</p>
            )}
            {(data.fields_truncated || data.metadata_truncated || data.problems_truncated) && (
              <p>部分字段或证据未展开；这里不是完整原始回执。</p>
            )}
            {fields
              .filter((field) => !workspace || field.request_id === plotId)
              .map((field) => {
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
                const xLabel = coordinateId
                  ? `${coordinateId} (${coordinate?.unit ?? "未知单位"})`
                  : "原始下标";
                const yUnit =
                  field.dtype === "complex128" && state.representation === "phase"
                    ? "rad"
                    : (field.unit ?? "未知单位");
                const yLabel = `${result.label ?? result.id} (${yUnit})`;
                const chartKey = JSON.stringify([
                  identity,
                  field.request_id,
                  activeStart,
                  state.representation,
                ]);
                return (
                  <section
                    key={field.request_id}
                    className="min-w-0 max-w-full [overflow-wrap:anywhere]"
                    style={
                      workspace
                        ? {
                            flex: 1,
                            minHeight: 0,
                            display: "flex",
                            flexDirection: "column",
                            overflow: "hidden",
                          }
                        : undefined
                    }
                  >
                    <p
                      className="text-xs shrink-0 truncate"
                      title={`${field.request_id} · ${field.complete ? "完整" : "局部"} ${field.start + 1}–${field.start + field.returned_count} / ${field.total_count ?? "未知"}`}
                    >
                      {field.request_id} · {field.complete ? "完整" : "局部"}{" "}
                      {field.returned_count ? field.start + 1 : 0}–
                      {field.start + field.returned_count} / {field.total_count ?? "未知"}
                      {!workspace && <> · 原始形状 [{field.shape.join(", ")}]</>}
                    </p>
                    {field.kind === "array" &&
                    field.shape.length === 1 &&
                    aligned &&
                    !field.unit_truncated &&
                    !coordinate?.unit_truncated ? (
                      <>
                        <p
                          className={
                            workspace
                              ? "shrink-0 truncate text-xs"
                              : "text-xs [overflow-wrap:anywhere]"
                          }
                          title={`纵轴 ${yLabel}；横轴 ${xLabel}`}
                        >
                          纵轴 {yLabel}；横轴 {xLabel}
                        </p>
                        <div
                          style={
                            workspace
                              ? {
                                  flex: 1,
                                  minHeight: 0,
                                  overflow: "hidden",
                                  overscrollBehavior: "contain",
                                }
                              : undefined
                          }
                        >
                          <EChart
                            recoveryHint={
                              workspace ? (
                                <p className="mt-2 text-sm">
                                  若重试仍失败，请先保存未保存的原结果，再用“查看已保存结果”在独立窗口打开。请勿刷新正在控制设备的窗口。
                                </p>
                              ) : undefined
                            }
                            ariaLabel={`${field.request_id} 局部曲线`}
                            height={workspace ? "fill" : 260}
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
                                name: xLabel,
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
                                name: yUnit,
                                scale: true,
                              },
                              // Match the pane, not the viewport. Full names/units stay in the
                              // wrapping caption; only duplicate in-chart names disappear.
                              // Restore them explicitly because ECharts merges media options.
                              media: [
                                {
                                  query: { maxWidth: 420 },
                                  // Three target intervals can leave only one label after
                                  // ECharts fits GHz-offset ticks into a narrow plot.
                                  option: {
                                    xAxis: { name: "", splitNumber: 2 },
                                    yAxis: { name: "" },
                                  },
                                },
                                {
                                  option: {
                                    xAxis: { name: xLabel, splitNumber: 3 },
                                    yAxis: { name: yUnit },
                                  },
                                },
                              ],
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
                        </div>
                      </>
                    ) : (
                      <p>此字段不具备对齐的一维坐标，保留数值窗口，不绘制推测曲线。</p>
                    )}
                  </section>
                );
              })}
            {workspace && fields.some((field) => !field.complete) && (
              <p className="shrink-0 text-xs">
                局部原始数据窗口，非全量概览；图内缩放仅作用于当前窗口。保存使用完整原件。
              </p>
            )}
          </div>
          {(!workspace || activeStart > 0 || next.length > 0) && (
            <div
              role="group"
              aria-label="局部窗口导航"
              className="flex shrink-0 flex-wrap items-center gap-2 text-xs"
            >
              <label>
                起始下标{" "}
                <input
                  className={`${formControl} w-28`}
                  aria-label="预览起始下标"
                  type="number"
                  min={0}
                  max={Number.MAX_SAFE_INTEGER}
                  value={activeStart}
                  onChange={(event) => {
                    const start = Number(event.target.value);
                    if (Number.isSafeInteger(start) && start >= 0) setWindow(start);
                  }}
                />
              </label>
              <button
                className={secondaryButton}
                disabled={activeStart === 0}
                onClick={() =>
                  setWindow(
                    previousStarts?.at(-1) ?? Math.max(0, activeStart - 512),
                    previousStarts?.slice(0, -1),
                  )
                }
              >
                上一页数据
              </button>
              <button
                className={secondaryButton}
                disabled={!next.length}
                onClick={() =>
                  setWindow(Math.min(...next), [...(previousStarts ?? []), activeStart])
                }
              >
                下一页数据
              </button>
            </div>
          )}
          {(!workspace || detailsOpen) && (
            <div
              role={workspace ? "region" : undefined}
              aria-label={workspace ? "数据详情" : undefined}
              style={
                workspace
                  ? {
                      flex: 1,
                      minHeight: 0,
                      overflow: "auto",
                      overscrollBehavior: "contain",
                      padding: 12,
                      background: "var(--color-surface, white)",
                      border: "1px solid var(--color-border, #ddd)",
                    }
                  : undefined
              }
            >
              {workspace && (
                <p className="text-xs">
                  连续原始数据窗口，不是全局概览或降采样。翻页查看其他范围；图内缩放仅作用于本窗口，切换窗口后重新缩放。保存始终使用完整原始数据。
                </p>
              )}
              <details
                open={state.dataOpen ?? false}
                onToggle={(event) => {
                  const dataOpen = event.currentTarget.open;
                  if (dataOpen !== (state.dataOpen ?? false)) update({ dataOpen });
                }}
              >
                <summary className="cursor-pointer text-xs">查看局部原始数值（按窗口）</summary>
                <div className="mt-2 grid min-w-0 grid-cols-1 gap-2 text-xs">
                  <label>
                    结果字段{" "}
                    <select
                      aria-label="精确结果字段"
                      className={formControl}
                      value={selected?.request_id ?? ""}
                      onChange={(event) =>
                        update(
                          workspace
                            ? { field: event.target.value, dataStart: 0, dataPreviousStarts: [] }
                            : { field: event.target.value, start: 0, previousStarts: [] },
                        )
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
                      {workspace && (
                        <p>
                          {selected.request_id} · {selected.complete ? "完整" : "局部"}{" "}
                          {selected.returned_count ? selected.start + 1 : 0}–
                          {selected.start + selected.returned_count} /{" "}
                          {selected.total_count ?? "未知"} · 原始形状 [{selected.shape.join(", ")}]
                        </p>
                      )}
                      {workspace && (
                        <p>
                          坐标约定：
                          {JSON.stringify(
                            acquisition?.results.find((field) => field.id === selected.request_id)
                              ?.axes ?? [],
                          )}
                        </p>
                      )}
                      <p>
                        {selected.dtype} · {selected.unit ?? "未知单位"}
                        {selected.unit_truncated ? "（单位已截断）" : ""} · C-order 原始下标；int64
                        保留十进制原值，超出安全整数范围不绘图。
                      </p>
                      {selected.reason && <p>{selected.reason}</p>}
                      <div
                        role="region"
                        aria-label="局部原始数值表"
                        className="max-h-72 overflow-auto"
                      >
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
            </div>
          )}
        </div>
      )}
    </section>
  );
}
