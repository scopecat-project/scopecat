import { lastMeasurementWindowOffset } from "./measurement-window";
import { measurementSelectionSchemaKey } from "./measurement-view-state";
import { Field, Select, Input, Notice } from "../../ui/Controls";
import {
  useMeasurementFocus,
  useMeasurementInteractions,
  useMeasurementSelections,
  type MeasurementViewSelection,
} from "./MeasurementWorkspace";
import { classes } from "../../ui/styles";
import { MeasurementRecordTable } from "./MeasurementRecordTable";
import { useEffect, useMemo, useRef, useState, type SetStateAction } from "react";
import { LoaderCircle } from "lucide-react";
import type {
  MeasurementTracePreview,
  MeasurementDatasetSchema,
  MeasurementRecord,
} from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import type { MeasurementPreview, MeasurementSlicePreview } from "../../types";
import { EChart } from "../../ui/EChart";
import { measurementChartOption } from "./chart-options";
import {
  measurementEntityAxes,
  measurementTable,
  measurementSlicePlan,
  measurementSliceAxisValue,
  measurementSliceAxisValueIsDuplicated,
  measurementTraceChart,
  measurementTraceStatus,
  planMeasurementCharts,
  type MeasurementChartPlan,
  type MeasurementChartSeries,
  type MeasurementEntityAxis,
  type MeasurementEntitySelection,
  type MeasurementSliceAxis,
  type MeasurementSliceSelection,
  type MeasurementTraceQueryPlan,
} from "./measurement-visualization";

const MAX_SLICE_SELECT_OPTIONS = 256;
const MAX_ENTITY_CHIPS = 12;
const MULTIPLE_ENTITY_SELECTION = "__multiple_entities__";

export function MeasurementDataPreview({
  preview,
  slice,
  sliceError,
  slicePending,
  fixedAxisIndices,
  measurementAxes,
  onMeasurementAxesChange,
  onSliceOffsetChange,
  onFixedAxisIndexChange,
  tracePlans = [],
  selectedTracePlanId,
  tracePreview,
  tracePending = false,
  traceRefreshing = false,
  traceError = null,
  onTracePlanChange,
  onEntitySelectionChange,
}: {
  preview: MeasurementPreview;
  slice?: MeasurementSlicePreview;
  sliceError: Error | null;
  slicePending: boolean;
  fixedAxisIndices: Record<string, number>;
  measurementAxes?: MeasurementSliceSelection;
  onMeasurementAxesChange?: (selection: MeasurementSliceSelection) => void;
  onSliceOffsetChange?: (offset: number) => void;
  onFixedAxisIndexChange: (axisId: string, index: number) => void;
  tracePlans?: MeasurementTraceQueryPlan[];
  selectedTracePlanId?: string;
  tracePreview?: MeasurementTracePreview;
  tracePending?: boolean;
  traceRefreshing?: boolean;
  traceError?: Error | null;
  onTracePlanChange?: (planId: string) => void;
  onEntitySelectionChange?: (selection: MeasurementEntitySelection) => void;
}) {
  const focused = useMeasurementFocus();
  const slicePlan = useMemo(
    () => measurementSlicePlan(preview.schema, measurementAxes),
    [preview.schema, measurementAxes],
  );
  const schemaKey = measurementSelectionSchemaKey(preview.schema);
  const retainedSelection = useMeasurementSelections();
  const [localSelection, setLocalSelection] = useState<MeasurementViewSelection>({
    schemaKey,
    entities: {},
  });
  const savedSelection = retainedSelection?.value ?? localSelection;
  const setSavedSelection = retainedSelection?.set ?? setLocalSelection;
  const requestedChartId =
    savedSelection.schemaKey === schemaKey ? savedSelection.chartId : undefined;
  const selectedEntityIdentities = useMemo(
    () => (savedSelection.schemaKey === schemaKey ? savedSelection.entities : {}),
    [savedSelection, schemaKey],
  );
  const setRequestedChartId = (chartId: string) =>
    setSavedSelection((current) => ({
      schemaKey,
      entities: current.schemaKey === schemaKey ? current.entities : {},
      chartId,
    }));
  const setSelectedEntityIdentities = (update: SetStateAction<Record<string, string[]>>) =>
    setSavedSelection((current) => {
      const entities = current.schemaKey === schemaKey ? current.entities : {};
      return {
        schemaKey,
        chartId: current.schemaKey === schemaKey ? current.chartId : undefined,
        entities: typeof update === "function" ? update(entities) : update,
      };
    });
  const selectionUnavailable =
    savedSelection.schemaKey !== "" && savedSelection.schemaKey !== schemaKey;
  const supportsAxisSelection = (slicePlan?.scalarObservableIds.length ?? 0) > 0;
  const chartSchema = slice?.schema ?? preview.schema;
  const entityAxes = useMemo(() => measurementEntityAxes(preview.schema), [preview.schema]);
  const missingSelectedEntities = Object.entries(selectedEntityIdentities).some(
    ([axisId, selected]) =>
      selected.some(
        (identity) =>
          !entityAxes
            .find((axis) => axis.id === axisId)
            ?.members.some((member) => member.identity === identity),
      ),
  );
  const entitySelection = useMemo(
    () =>
      Object.fromEntries(
        entityAxes.flatMap((axis) => {
          const selected = selectedEntityIdentities[axis.id];
          if (!selected) return [];
          const selectedSet = new Set(selected);
          const indices = axis.members.flatMap((member) =>
            selectedSet.has(member.identity) ? [member.index] : [],
          );
          return indices.length > 0 ? [[axis.id, indices] as const] : [];
        }),
      ),
    [entityAxes, selectedEntityIdentities],
  );
  useEffect(() => {
    onEntitySelectionChange?.(entitySelection);
  }, [entitySelection, onEntitySelectionChange]);
  // A one-axis scan has no fixed-slice ambiguity. Use the existing bounded
  // preview plus latest receipt, without inventing the buffered intermediate points.
  const liveScalarScan =
    slicePlan?.varyingAxes.length === 1 &&
    slicePlan.fixedAxes.length === 0 &&
    preview.livePointIndex !== undefined &&
    !slice?.truncated &&
    !sliceError;
  const plotRecords = useMemo(() => {
    if (!liveScalarScan) return slicePlan ? (slice?.items ?? []) : preview.items;
    const records = new Map((slice?.items ?? []).map((record) => [record.point_index, record]));
    for (const record of preview.items)
      if (!records.has(record.point_index)) records.set(record.point_index, record);
    return [...records.values()].sort((a, b) => a.point_index - b.point_index);
  }, [liveScalarScan, slicePlan, slice, preview.items]);
  const plotIncludesLive = preview.livePointIndex !== undefined && (!slicePlan || liveScalarScan);
  const scalarWindow = Boolean(
    slice?.truncated &&
    slicePlan?.varyingAxes.length === 1 &&
    slicePlan.scalarObservableIds.length > 0 &&
    slicePlan.numericAxes.some((axis) => axis.id === slicePlan.varyingAxes[0]?.id),
  );
  const lastWindowOffset =
    scalarWindow && slicePlan?.fixedAxes.length === 0 && slice
      ? lastMeasurementWindowOffset(slice.selectedPointCount)
      : undefined;
  const charts = useMemo(() => {
    if (!slicePlan) {
      return planMeasurementCharts(preview.items, preview.schema, undefined, entitySelection);
    }
    return planMeasurementCharts(
      slice?.truncated && !scalarWindow ? [] : plotRecords,
      chartSchema,
      fixedAxisIndices,
      entitySelection,
      measurementAxes,
    )
      .filter(
        (chart) =>
          !scalarWindow ||
          (chart.kind !== "heatmap" &&
            chart.series.every((series) => slicePlan.scalarObservableIds.includes(series.id))),
      )
      .map((chart) =>
        scalarWindow && chart.kind !== "heatmap"
          ? {
              ...chart,
              kind: "scatter" as const,
              note: [
                "当前窗口，非全量数据。仅绘制此已保存窗口内的可用标量值，缺口不连线。",
                chart.note,
              ]
                .filter(Boolean)
                .join(" "),
            }
          : liveScalarScan && chart.kind === "line"
            ? {
                ...chart,
                kind: "scatter" as const,
                note: "Partial received data; gaps are not interpolated.",
              }
            : chart,
      );
  }, [
    plotRecords,
    measurementAxes,
    liveScalarScan,
    scalarWindow,
    chartSchema,
    entitySelection,
    fixedAxisIndices,
    preview.items,
    preview.schema,
    slice,
    slicePlan,
  ]);
  const selectedTracePlan =
    tracePlans.find((plan) => plan.id === selectedTracePlanId) ?? tracePlans[0];
  // Run changes already remount MeasurementRecords. Reset within a run when the
  // selected domain changes; incoming records and receipt counts are not identity.
  const viewScope = JSON.stringify([
    fixedAxisIndices,
    measurementAxes,
    entitySelection,
    slice?.offset ?? 0,
    preview.schema?.dataset_id,
    preview.schema?.variables,
    preview.schema?.point_domain,
    preview.schema?.dimensions,
  ]);
  const [tableOpen, setTableOpen] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const tableRecords = slicePlan ? (slice?.items ?? []) : preview.items;
  const selectAllEntities = (axisId: string) => {
    setSelectedEntityIdentities((current) => {
      const next = { ...current };
      delete next[axisId];
      return next;
    });
  };
  const selectOneEntity = (axisId: string, identity: string) => {
    setSelectedEntityIdentities((current) => ({ ...current, [axisId]: [identity] }));
  };
  const toggleEntity = (axis: MeasurementEntityAxis, identity: string) => {
    setSelectedEntityIdentities((current) => {
      const existing = current[axis.id];
      if (!existing) return { ...current, [axis.id]: [identity] };
      const selected = existing.includes(identity)
        ? existing.filter((candidate) => candidate !== identity)
        : [...existing, identity];
      const next = { ...current };
      if (selected.length === 0 || selected.length === axis.size) delete next[axis.id];
      else next[axis.id] = selected;
      return next;
    });
  };
  return (
    <div
      className={classes(
        "rounded-md border border-line bg-panel",
        focused ? "flex min-h-0 flex-1 flex-col overflow-auto [&>*]:shrink-0" : "overflow-hidden",
      )}
    >
      <div
        className={classes(
          "flex items-center justify-between gap-3 border-b border-line px-3 py-2 text-xs font-bold tracking-[0.06em] text-text-dim uppercase",
          focused && "hidden",
        )}
      >
        <strong className="text-sm text-text-soft">Measurement data</strong>
        <span>{preview.schema ? "Schema" : "Live"}</span>
      </div>
      {selectionUnavailable && <p role="status">测量结构已变化；图表与实体选择已恢复默认值。</p>}
      {missingSelectedEntities && (
        <p role="status">部分已选实体不再可用；仅保留匹配的实体，若无匹配则显示默认实体选择。</p>
      )}
      <section
        aria-label="测量数据摘要"
        className={classes(
          "border-b border-line px-3 text-xs text-text-soft",
          focused ? "py-1" : "py-2",
        )}
      >
        <div className="flex flex-wrap gap-x-5 gap-y-1">
          <span>
            run 已接收 <strong>{preview.recordCount ?? "未知"}</strong> 条
          </span>
          <span>
            run 已保存 <strong>{preview.durableRecordCount ?? "未知"}</strong> 条
          </span>
          <span>
            当前记录预览 <strong>{plotRecords.length}</strong> 条记录
          </span>
          {slicePlan && (
            <span>
              当前切片已读取 <strong>{slice?.items.length ?? "未知"}</strong> 条记录
            </span>
          )}
        </div>
        {slicePending && (
          <p className="mt-2" role="status">
            正在读取当前切片，图表可能仍显示上次读取的数据。
          </p>
        )}
        {sliceError && (
          <p className="mt-2 text-yellow" role="alert">
            当前切片读取失败，数据可能已过时： {errorMessage(sliceError)}
          </p>
        )}
        {preview.livePointIndex !== undefined && (
          <p className="mt-2 text-blue">
            最新接收记录（点索引 {preview.livePointIndex}）尚未保存。
          </p>
        )}
        {preview.recordCount !== undefined &&
          preview.durableRecordCount !== undefined &&
          preview.recordCount > preview.durableRecordCount && (
            <p className="mt-2">
              {preview.recordCount - preview.durableRecordCount}{" "}
              条已接收记录尚未保存；最新记录不代表中间记录全部可见。
            </p>
          )}
        {(plotIncludesLive || preview.truncated || slice?.truncated) && (
          <p className="mt-2">这是有界预览，缺口不插值；当前可见数量不代表 run 总数或连续覆盖。</p>
        )}
      </section>

      {slicePlan &&
        !liveScalarScan &&
        (onMeasurementAxesChange ||
          slicePlan.fixedAxes.length > 0 ||
          slice?.truncated ||
          sliceError ||
          slice?.items.length !== slice?.selectedPointCount) && (
          <div
            className="flex flex-wrap items-end justify-between gap-2 border-b border-line bg-panel-soft px-3 py-2"
            data-testid="measurement-slice-controls"
          >
            <div className="grid w-full grid-cols-2 items-end gap-2 sm:flex sm:w-auto sm:flex-wrap">
              {onMeasurementAxesChange &&
                supportsAxisSelection &&
                slicePlan.numericAxes.length > 0 && (
                  <>
                    <Field>
                      Horizontal axis
                      <Select
                        aria-label="Horizontal axis"
                        value={slicePlan.varyingAxes[0]?.id}
                        onChange={(event) =>
                          onMeasurementAxesChange({
                            xAxisId: event.target.value,
                            yAxisId:
                              slicePlan.varyingAxes[1]?.id === event.target.value
                                ? undefined
                                : slicePlan.varyingAxes[1]?.id,
                          })
                        }
                      >
                        {slicePlan.numericAxes.map((axis) => (
                          <option key={axis.id} value={axis.id}>
                            {axis.label}
                            {axis.unit ? ` [${axis.unit}]` : ""}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field>
                      Vertical axis
                      <Select
                        aria-label="Vertical axis"
                        value={slicePlan.varyingAxes[1]?.id ?? ""}
                        onChange={(event) =>
                          onMeasurementAxesChange({
                            xAxisId: slicePlan.varyingAxes[0]!.id,
                            yAxisId: event.target.value || undefined,
                          })
                        }
                      >
                        <option value="">None · signal curve</option>
                        {slicePlan.numericAxes
                          .filter((axis) => axis.id !== slicePlan.varyingAxes[0]?.id)
                          .map((axis) => (
                            <option key={axis.id} value={axis.id}>
                              {axis.label}
                              {axis.unit ? ` [${axis.unit}]` : ""}
                            </option>
                          ))}
                      </Select>
                    </Field>
                  </>
                )}
              {slicePlan.fixedAxes.map((axis) => {
                const selectedIndex = fixedAxisIndices[axis.id] ?? 0;
                return (
                  <div
                    className="grid min-w-0 gap-1 text-xs font-bold tracking-[0.04em] text-text-dim uppercase"
                    key={axis.id}
                  >
                    <span>{axis.label} slice</span>
                    <span className="flex items-center gap-1">
                      <button
                        type="button"
                        aria-label={`Previous ${axis.label} value`}
                        disabled={selectedIndex === 0}
                        className="rounded border border-line px-2 py-1 disabled:opacity-35"
                        onClick={() => onFixedAxisIndexChange(axis.id, selectedIndex - 1)}
                      >
                        −
                      </button>
                      {axis.size <= MAX_SLICE_SELECT_OPTIONS ? (
                        <Select
                          aria-label={`${axis.label} slice`}
                          className="w-full min-w-0 sm:w-auto sm:min-w-28"
                          value={selectedIndex}
                          onChange={(event) =>
                            onFixedAxisIndexChange(axis.id, Number(event.target.value))
                          }
                        >
                          {Array.from({ length: axis.size }, (_value, index) => (
                            <option key={index} value={index}>
                              {sliceAxisOption(axis, index)}
                            </option>
                          ))}
                        </Select>
                      ) : (
                        <MeasurementSliceIndex
                          key={`${axis.id}:${selectedIndex}`}
                          axis={axis}
                          selectedIndex={selectedIndex}
                          onCommit={(index) => onFixedAxisIndexChange(axis.id, index)}
                        />
                      )}
                      <button
                        type="button"
                        aria-label={`Next ${axis.label} value`}
                        disabled={selectedIndex + 1 >= axis.size}
                        className="rounded border border-line px-2 py-1 disabled:opacity-35"
                        onClick={() => onFixedAxisIndexChange(axis.id, selectedIndex + 1)}
                      >
                        +
                      </button>
                    </span>
                    {axis.size > MAX_SLICE_SELECT_OPTIONS && (
                      <span className="max-w-52 truncate font-medium tracking-normal text-text-soft normal-case">
                        {sliceAxisOption(axis, selectedIndex)} · {axis.size.toLocaleString()} values
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
            <p className="text-xs text-text-soft" data-testid="measurement-slice-summary">
              Current slice:{" "}
              {slicePlan.varyingAxes
                .map((axis) => `${axis.label}${axis.unit ? ` [${axis.unit}]` : ""} varying`)
                .join(" × ")}
              {slicePlan.fixedAxes
                .map(
                  (axis) =>
                    ` · ${axis.label} = ${sliceAxisOption(axis, fixedAxisIndices[axis.id] ?? 0)}`,
                )
                .join("")}
            </p>
            <div className="flex flex-wrap items-center justify-end gap-2 text-xs text-text-dim">
              <span
                className="inline-flex items-center gap-1.5"
                role={sliceError ? "alert" : "status"}
              >
                {slicePending && (
                  <LoaderCircle className="animate-spin" size={12} aria-hidden="true" />
                )}
                {sliceStatus(slice, sliceError, slicePending)}
              </span>
              {slice && (slice.previousOffset !== undefined || slice.nextOffset !== undefined) && (
                <span className="inline-flex items-center gap-1">
                  <button
                    aria-label="Previous measurement window"
                    className="rounded border border-line bg-panel px-2 py-1 font-bold text-text-soft disabled:opacity-35"
                    disabled={slicePending || slice.previousOffset === undefined}
                    onClick={() => onSliceOffsetChange?.(slice.previousOffset ?? 0)}
                    type="button"
                  >
                    Previous
                  </button>
                  <button
                    aria-label="Next measurement window"
                    className="rounded border border-line bg-panel px-2 py-1 font-bold text-text-soft disabled:opacity-35"
                    disabled={slicePending || slice.nextOffset === undefined}
                    onClick={() => onSliceOffsetChange?.(slice.nextOffset ?? slice.offset)}
                    type="button"
                  >
                    Next
                  </button>
                  {lastWindowOffset !== undefined && (
                    <button
                      aria-label="查看末窗口"
                      title="按逻辑点总数定位，不代表最新到达的记录"
                      className="rounded border border-line bg-panel px-2 py-1 font-bold text-text-soft disabled:opacity-35"
                      disabled={
                        slicePending ||
                        Boolean(sliceError) ||
                        slice.offset === lastWindowOffset ||
                        !onSliceOffsetChange
                      }
                      onClick={() => onSliceOffsetChange?.(lastWindowOffset)}
                      type="button"
                    >
                      末窗口
                    </button>
                  )}
                </span>
              )}
            </div>
          </div>
        )}

      {entityAxes.length > 0 && (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel-soft px-3 py-2"
          data-testid="measurement-entity-controls"
        >
          <strong className="text-xs tracking-[0.06em] text-text-dim uppercase">Entities</strong>
          {entityAxes.map((axis) => {
            const availableIdentities = new Set(axis.members.map((member) => member.identity));
            const requested = selectedEntityIdentities[axis.id];
            const validSelection = requested?.filter((identity) =>
              availableIdentities.has(identity),
            );
            const selected =
              validSelection && validSelection.length > 0 ? validSelection : undefined;
            const allSelected = selected === undefined;
            return (
              <div className="flex min-w-0 flex-wrap items-center gap-1.5" key={axis.id}>
                <span className="mr-0.5 text-xs font-semibold text-text-soft">{axis.label}</span>
                <button
                  aria-pressed={allSelected}
                  className={entityChipClasses(allSelected)}
                  onClick={() => selectAllEntities(axis.id)}
                  type="button"
                >
                  All {axis.size}
                </button>
                {axis.size <= MAX_ENTITY_CHIPS ? (
                  axis.members.map((member) => {
                    const pressed = selected?.includes(member.identity) ?? false;
                    return (
                      <button
                        aria-label={`${axis.label} ${member.label}`}
                        aria-pressed={pressed}
                        className={entityChipClasses(pressed)}
                        key={member.identity}
                        onClick={() => toggleEntity(axis, member.identity)}
                        title={member.kind ? `${member.kind}:${member.id}` : member.id}
                        type="button"
                      >
                        {member.label}
                      </button>
                    );
                  })
                ) : (
                  <Select
                    aria-label={`${axis.label} entity`}
                    className="max-w-52"
                    onChange={(event) => {
                      if (event.target.value === "all") selectAllEntities(axis.id);
                      else if (event.target.value !== MULTIPLE_ENTITY_SELECTION) {
                        selectOneEntity(axis.id, event.target.value);
                      }
                    }}
                    value={
                      selected === undefined
                        ? "all"
                        : selected.length === 1
                          ? selected[0]
                          : MULTIPLE_ENTITY_SELECTION
                    }
                  >
                    <option value="all">All {axis.size}</option>
                    {selected && selected.length > 1 && (
                      <option disabled value={MULTIPLE_ENTITY_SELECTION}>
                        {selected.length} selected
                      </option>
                    )}
                    {axis.members.map((member) => (
                      <option key={member.identity} value={member.identity}>
                        {member.label}
                      </option>
                    ))}
                  </Select>
                )}
                <span className="text-xs text-text-dim">
                  {allSelected ? "Comparing all" : `${selected.length} selected`}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {tracePlans.length > 0 && (
        <div
          className={classes(
            "border-b border-line p-2.5",
            focused && "flex min-h-64 flex-1 flex-col",
          )}
          data-testid="measurement-trace-preview"
        >
          <p className="mb-2 text-xs text-text-dim">
            Each query reads a finite saved snapshot; refreshing issues a new query.
          </p>
          <div
            className="mb-2.5 flex flex-wrap items-center justify-between gap-2 text-xs text-text-dim"
            hidden={charts.length === 1}
          >
            <Field>
              Trace
              <Select
                aria-label="Measurement trace"
                className="max-w-[min(70vw,420px)]"
                onChange={(event) => onTracePlanChange?.(event.target.value)}
                value={selectedTracePlan?.id ?? ""}
              >
                {tracePlans.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <MeasurementTraceResult
            key={`${viewScope}:${selectedTracePlan?.id}`}
            interactionScope={viewScope}
            preview={tracePreview}
            pending={tracePending}
            refreshing={traceRefreshing}
            error={traceError}
          />
        </div>
      )}

      {charts.length > 0 ? (
        <MeasurementChartPicker
          key={viewScope}
          interactionScope={viewScope}
          charts={charts}
          requestedChartId={requestedChartId}
          onChartChange={setRequestedChartId}
        />
      ) : tracePlans.length === 0 ? (
        <p className="m-0 border-b border-line px-3 py-2.5 text-sm leading-normal text-text-dim">
          {emptyChartMessage({
            usesSemanticSlice: slicePlan !== undefined,
            scalarWindow,
            slice,
            sliceError,
            slicePending,
            preview,
          })}
        </p>
      ) : null}

      {charts.length > 0 && (
        <details className="border-b border-line px-3 py-2 text-xs text-text-soft">
          <summary className="cursor-pointer">图表数据来源与点索引</summary>
          <p data-testid="plot-position" className="mt-2">
            Plot source: {plotIncludesLive ? "saved and received preview" : "saved preview"} ·{" "}
            {plotRecords.length} available records
            {plotRecords.length > 0 &&
              ` · highest point index ${Math.max(...plotRecords.map((record) => record.point_index))}`}
            {" · 最高点索引不代表此前所有点均已接收、保存或显示。"}
            {liveScalarScan && " Buffered intermediate records may not be in this view."}
          </p>
        </details>
      )}
      {(slicePlan || tracePlans.length > 0 || entityAxes.length > 0) && (
        <details
          className={classes(
            "border-b border-line bg-panel-soft px-3 py-2 text-xs text-text-dim",
            focused && "hidden",
          )}
        >
          <summary className="cursor-pointer">About this data view</summary>
          <p className="mt-2">
            Product-grid scalar signals offer numeric plot axes; other dimensions stay fixed at
            authored positions. Text, boolean and opaque coordinates are not treated as continuous
            axes. Point clouds and array-local traces keep their existing views. One-axis scans may
            overlay the latest received record; gaps in that preview are not interpolated. Heatmaps
            and the table use the selected saved slice when available. Trace previews are bounded by
            the server for the selected authored domain and entities. Entity selection also filters
            comparison plots and table summaries. Large slices expose bounded logical-point windows
            without losing their authored-axis context. The JSON preview retains the bounded run
            view, including the latest live daemon receipt.
          </p>
        </details>
      )}

      <details
        onToggle={(event) => setTableOpen(event.currentTarget.open)}
        className={classes("border-t border-line text-xs text-text-soft", focused && "hidden")}
      >
        <summary className="cursor-pointer px-3 py-2 font-semibold">
          Record table · {tableRecords.length} rows in this view
        </summary>
        {tableOpen && !focused && (
          <DeferredRecordTable
            records={tableRecords}
            schema={slicePlan ? chartSchema : preview.schema}
            entitySelection={entitySelection}
          />
        )}
      </details>

      <details
        onToggle={(event) => setRawOpen(event.currentTarget.open)}
        className={classes("border-t border-line text-xs text-text-dim", focused && "hidden")}
      >
        <summary className="cursor-pointer px-3 py-2 font-bold hover:text-text-soft">
          <span>Raw records</span>
          <span className="ml-1 font-medium text-text-dim">in preview</span>
        </summary>
        {rawOpen && !focused && <DeferredRawRecords records={preview.items} />}
      </details>
    </div>
  );
}

function DeferredRecordTable({
  records,
  schema,
  entitySelection,
}: {
  records: MeasurementRecord[];
  schema?: MeasurementDatasetSchema;
  entitySelection: MeasurementEntitySelection;
}) {
  const table = useMemo(
    () => measurementTable(records, schema, entitySelection),
    [records, schema, entitySelection],
  );
  return (
    <div className="max-h-80 overflow-auto">
      <MeasurementRecordTable table={table} />
    </div>
  );
}

function DeferredRawRecords({ records }: { records: MeasurementRecord[] }) {
  const json = useMemo(() => JSON.stringify(records, null, 2), [records]);
  return (
    <pre
      className="m-0 max-h-[320px] overflow-auto border-t border-line bg-panel-soft p-3 text-xs leading-[1.5] text-text-soft"
      data-testid="measurement-preview"
    >
      {json}
    </pre>
  );
}

export function MeasurementTraceResult({
  interactionScope,
  preview,
  pending,
  refreshing = false,
  error,
}: {
  interactionScope?: string;
  preview?: MeasurementTracePreview;
  pending: boolean;
  refreshing?: boolean;
  error: Error | null;
}) {
  const chart = useMemo(
    () => (pending || error ? undefined : measurementTraceChart(preview)),
    [preview, pending, error],
  );
  return (
    <>
      <p role={error ? "alert" : "status"}>
        {tracePreviewStatus(preview, error, pending, refreshing)}
      </p>
      {chart && <MeasurementChart chart={chart} interactionScope={interactionScope} />}
      {!pending && !error && preview && <TraceAvailabilityDetails preview={preview} />}
      {!pending && !error && preview && !chart && <p>本次有界选择未返回已持久保存且可用的序列。</p>}
    </>
  );
}

function tracePreviewStatus(
  preview: MeasurementTracePreview | undefined,
  error: Error | null,
  pending: boolean,
  refreshing: boolean,
): string {
  if (error) return `波形不可用： ${errorMessage(error)}`;
  if (pending) return "正在读取有界波形预览…";
  if (!preview) return "等待有界波形预览";
  if (refreshing) return "正在刷新有界波形；图表显示上次成功读取的数据。";
  return measurementTraceStatus(preview);
}

export function TraceAvailabilityDetails({ preview }: { preview: MeasurementTracePreview }) {
  const incomplete = preview.series.filter(
    (series) => series.available_sample_count < series.source_sample_count,
  );
  if (preview.failures.length === 0 && incomplete.length === 0 && !preview.entity_acquisition) {
    return null;
  }
  const acquisition = preview.entity_acquisition;
  return (
    <div
      className="mt-2 grid gap-1.5 rounded border border-line bg-panel-soft p-2 text-xs text-text-dim"
      data-testid="measurement-trace-availability"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <strong className="text-text-soft">
          {[
            preview.failures.length > 0
              ? `${preview.failures.length.toLocaleString()} 条不可用`
              : undefined,
            incomplete.length > 0 ? `${incomplete.length.toLocaleString()} 条不完整` : undefined,
          ]
            .filter(Boolean)
            .join(" · ") || "对象采集"}
        </strong>
        {acquisition && (
          <span>
            {acquisition.policy.replaceAll("_", " ")}
            {acquisition.cohort_id ? ` · cohort ${acquisition.cohort_id}` : ""}
          </span>
        )}
      </div>
      {preview.failures.map((failure, index) => (
        <details
          className="rounded border border-line bg-panel px-2 py-1.5"
          key={`${failure.point_index}:${failure.entity_index ?? "point"}:${index}`}
        >
          <summary className="cursor-pointer text-text-soft">
            <strong>{failure.label}</strong>
            <span className="ml-1.5 text-red">不可用 · {failure.reasons.join(", ")}</span>
          </summary>
          {failure.evidence ? (
            <TraceEvidence evidence={failure.evidence} />
          ) : (
            <p className="mt-1.5 mb-0">未记录采集证据。</p>
          )}
        </details>
      ))}
      {incomplete.map((series, index) => (
        <details
          className="rounded border border-line bg-panel px-2 py-1.5"
          key={`${series.point_index}:${series.entity_index ?? "point"}:partial:${index}`}
        >
          <summary className="cursor-pointer text-text-soft">
            <strong>{series.label}</strong>
            <span className="ml-1.5 text-yellow">
              {series.available_sample_count}/{series.source_sample_count} 个采样点可用
              {series.unavailable_reasons.length > 0
                ? ` · ${series.unavailable_reasons.join(", ")}`
                : ""}
            </span>
          </summary>
          {series.evidence ? (
            <TraceEvidence evidence={series.evidence} />
          ) : (
            <p className="mt-1.5 mb-0">未记录采集证据。</p>
          )}
        </details>
      ))}
    </div>
  );
}

function TraceEvidence({
  evidence,
}: {
  evidence: NonNullable<MeasurementTracePreview["failures"][number]["evidence"]>;
}) {
  return (
    <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1">
      <dt>结果</dt>
      <dd className="m-0 font-mono text-text-soft">
        {evidence.instrument_id} · {evidence.acquisition_id} → {evidence.result_id}
      </dd>
      <dt>命令</dt>
      <dd className="m-0 font-mono text-text-soft">{evidence.command_id}</dd>
      <dt>接口</dt>
      <dd className="m-0 font-mono text-text-soft">{evidence.interface_id}</dd>
      {evidence.component_path.length > 0 && (
        <>
          <dt>部件</dt>
          <dd className="m-0 font-mono text-text-soft">{evidence.component_path.join(" / ")}</dd>
        </>
      )}
      <dt>时间范围</dt>
      <dd className="m-0 font-mono text-text-soft">
        <time dateTime={evidence.started_at}>{evidence.started_at}</time> →{" "}
        <time dateTime={evidence.completed_at}>{evidence.completed_at}</time>
      </dd>
    </dl>
  );
}

function sliceStatus(
  slice: MeasurementSlicePreview | undefined,
  error: Error | null,
  pending: boolean,
): string {
  if (error) return `Slice unavailable: ${errorMessage(error)}`;
  if (pending) return "Reading selected slice…";
  if (!slice) return "Waiting for selected slice";
  if (slice.truncated) {
    const firstPoint = slice.windowPointCount > 0 ? slice.offset + 1 : 0;
    const lastPoint = slice.offset + slice.windowPointCount;
    return `Points ${firstPoint.toLocaleString()}–${lastPoint.toLocaleString()} of ${slice.selectedPointCount.toLocaleString()} · ${slice.items.length.toLocaleString()} durable`;
  }
  return `${slice.items.length.toLocaleString()} of ${slice.selectedPointCount.toLocaleString()} slice points durable`;
}

function emptyChartMessage({
  usesSemanticSlice,
  scalarWindow,
  slice,
  sliceError,
  slicePending,
  preview,
}: {
  usesSemanticSlice: boolean;
  scalarWindow: boolean;
  slice?: MeasurementSlicePreview;
  sliceError: Error | null;
  slicePending: boolean;
  preview: MeasurementPreview;
}): string {
  if (usesSemanticSlice) {
    if (sliceError) return "The selected product-grid slice could not be read.";
    if (slicePending || !slice) return "Reading the selected product-grid slice before plotting.";
    if (scalarWindow) return "当前窗口，非全量数据。此窗口内暂无已保存的可用标量值。";
    if (slice.truncated) {
      return "The selected slice is too large for an automatic plot. Use a projected Arrow reader for analysis.";
    }
    if (slice.items.length === 0) {
      return "No saved records in the selected slice. Planned points may not have been acquired or saved yet.";
    }
    if (slice.items.length < slice.selectedPointCount) {
      return `The selected slice is incomplete: ${slice.items.length.toLocaleString()} of ${slice.selectedPointCount.toLocaleString()} points are durable. A complete grid is not available in this saved slice.`;
    }
  }
  if (preview.items.length === 0) {
    return "No saved measurement records are available for this plot.";
  }
  return "No safe automatic plot is available for these variable shapes. The typed table remains available below.";
}

function MeasurementSliceIndex({
  axis,
  selectedIndex,
  onCommit,
}: {
  axis: MeasurementSliceAxis;
  selectedIndex: number;
  onCommit: (index: number) => void;
}) {
  const [draft, setDraft] = useState<string>();
  const commit = () => {
    if (draft === undefined) return;
    const position = Number(draft);
    if (
      draft.trim() !== "" &&
      Number.isInteger(position) &&
      position >= 1 &&
      position <= axis.size
    ) {
      if (position - 1 !== selectedIndex) onCommit(position - 1);
    }
    setDraft(undefined);
  };
  return (
    <Input
      aria-label={`${axis.label} slice index`}
      className="w-full min-w-0 sm:w-28"
      max={axis.size}
      min={1}
      step={1}
      type="number"
      title={`Position 1–${axis.size}; press Enter or leave the field to apply`}
      value={draft ?? String(selectedIndex + 1)}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        }
        if (event.key === "Escape") setDraft(undefined);
      }}
    />
  );
}

function sliceAxisOption(axis: MeasurementSliceAxis, index: number): string {
  const scalar = measurementSliceAxisValue(axis, index);
  if (!scalar) return `Index ${index + 1}`;
  const value =
    typeof scalar.value === "object"
      ? `${scalar.value.real}${scalar.value.imag < 0 ? "" : "+"}${scalar.value.imag}i`
      : String(scalar.value);
  const unit = scalar.unit ?? axis.unit;
  const duplicate = measurementSliceAxisValueIsDuplicated(axis, index);
  return `${value}${unit ? ` ${unit}` : ""}${duplicate ? ` · Index ${index + 1}` : ""}`;
}

export function MeasurementChartPicker({
  interactionScope,
  charts,
  requestedChartId,
  onChartChange,
}: {
  interactionScope?: string;
  charts: MeasurementChartPlan[];
  requestedChartId?: string;
  onChartChange?: (id: string) => void;
}) {
  const focused = useMeasurementFocus();
  const [localChartId, setLocalChartId] = useState<string>();
  const signalViews = charts.every((chart) => chart.id.startsWith("slice:"));
  const selectedChart =
    charts.find((chart) => chart.id === (requestedChartId ?? localChartId)) ?? charts[0];
  return (
    <div
      className={classes("border-b border-line p-2.5", focused && "flex min-h-min flex-1 flex-col")}
      data-testid="measurement-charts"
    >
      <div
        className="mb-2.5 flex flex-wrap items-center justify-between gap-2 text-xs text-text-dim"
        hidden={charts.length === 1}
      >
        <span>
          {charts.length}{" "}
          {signalViews ? "个信号视图" : charts.length === 1 ? "个候选图表" : "个候选图表"}
        </span>
        {charts.length > 1 && (
          <Field>
            {signalViews ? "信号" : "图表"}
            <Select
              aria-label="测量图表"
              className="max-w-[min(70vw,420px)]"
              onChange={(event) => (onChartChange ?? setLocalChartId)(event.target.value)}
              value={selectedChart?.id ?? ""}
            >
              {charts.map((chart) => (
                <option key={chart.id} value={chart.id}>
                  {chartOptionLabel(chart)}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
      {selectedChart && (
        <MeasurementChart chart={selectedChart} interactionScope={interactionScope} />
      )}
    </div>
  );
}

export function MeasurementChart({
  chart,
  interactionScope,
}: {
  chart: MeasurementChartPlan;
  interactionScope?: string;
}) {
  const interactionStore = useMeasurementInteractions();
  const focused = useMeasurementFocus();
  const [expanded, setExpanded] = useState(false);
  const [resetRange, setResetRange] = useState(0);
  const hasPoints = chart.series.some((series) => series.points.length > 0);
  const plotArea = useRef<HTMLDivElement>(null);
  const [focusHeight, setFocusHeight] = useState(240);
  useEffect(() => {
    const element = plotArea.current;
    if (!focused || !element) return;
    const observer = new ResizeObserver(() => setFocusHeight(element.clientHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, [focused, hasPoints, chart.layout]);
  const [compact, setCompact] = useState(
    () => window.matchMedia?.("(max-height: 800px)").matches ?? false,
  );
  useEffect(() => {
    const query = window.matchMedia?.("(max-height: 800px)");
    if (!query) return;
    const update = () => setCompact(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const points = chart.series.flatMap((series) => series.points);
  const option = useMemo(() => measurementChartOption(chart), [chart]);
  const fixedCoordinates =
    chart.kind === "heatmap" ? fixedCoordinatesLabel(chart.fixedCoordinates) : undefined;
  const chartDescription = chart.colorLabel
    ? `${chart.title}: ${chart.yLabel} by ${chart.xLabel}, colored by ${chart.colorLabel}`
    : `${chart.title}: ${chart.yLabel} by ${chart.xLabel}`;
  const accessibleTitle = fixedCoordinates
    ? `${chartDescription}, fixed at ${fixedCoordinates}`
    : chartDescription;
  return (
    <figure
      className={classes(
        "m-0 min-w-0 rounded-md border border-line bg-panel-soft p-2.5",
        focused && "flex min-h-min flex-1 flex-col",
      )}
    >
      <figcaption className="mb-1.5 flex flex-wrap items-start justify-between gap-2">
        <span className="min-w-0 flex-1">
          <strong className="block text-sm text-text-soft">{chart.title}</strong>
          <span className="text-xs text-text-dim">
            {chart.xLabel} → {chart.yLabel}
            {chart.colorLabel ? ` · color: ${chart.colorLabel}` : ""}
            {fixedCoordinates ? ` · fixed: ${fixedCoordinates}` : ""}
          </span>
        </span>
        <span className="flex items-center gap-2 text-xs text-text-dim">
          {chart.kind !== "heatmap" && (
            <button
              type="button"
              onClick={() => setResetRange((value) => value + 1)}
              className="rounded-md border border-line px-2 py-1 text-xs text-text-soft hover:bg-panel-strong"
            >
              恢复自动范围
            </button>
          )}
          {focused === undefined && (
            <button
              type="button"
              aria-pressed={expanded}
              onClick={() => setExpanded(!expanded)}
              className="rounded-md border border-line px-3 py-1.5 text-xs text-text-soft hover:bg-panel-strong"
            >
              {expanded ? "标准图表高度" : "展开图表"}
            </button>
          )}
          {chart.layout === "small-multiples" ? "small multiples" : chart.kind.replace("-", " ")}
        </span>
      </figcaption>
      {points.length === 0 ? (
        <Notice role="status">所选切片中没有此信号的可用值。</Notice>
      ) : chart.layout === "small-multiples" && chart.series.length > 1 ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-2">
          {chart.series.map((series) => (
            <MeasurementSeriesPanel
              chart={chart}
              interactionScope={interactionScope}
              key={series.id}
              series={series}
              resetRange={resetRange}
            />
          ))}
        </div>
      ) : (
        <div ref={plotArea} className={focused ? "relative min-h-48 flex-1" : undefined}>
          <div className={focused ? "absolute inset-0" : undefined}>
            <EChart
              resetRange={resetRange}
              interactionKey={JSON.stringify([interactionScope, measurementInteractionKey(chart)])}
              interactionStore={interactionStore}
              ariaLabel={accessibleTitle}
              height={focused ? focusHeight : expanded ? 540 : compact ? 240 : 340}
              option={option}
              pointCount={points.length}
              seriesLabels={chart.series.map((series) => series.label)}
              seriesCount={chart.series.length}
            />
          </div>
        </div>
      )}
      {chart.note && <p className="mt-1.5 mb-0 text-xs text-text-dim">{chart.note}</p>}
    </figure>
  );
}

function MeasurementSeriesPanel({
  interactionScope,
  chart,
  series,
  resetRange,
}: {
  chart: MeasurementChartPlan;
  interactionScope?: string;
  series: MeasurementChartSeries;
  resetRange: number;
}) {
  const interactionStore = useMeasurementInteractions();
  const option = useMemo(
    () =>
      measurementChartOption({
        ...chart,
        layout: "overlay",
        series: [series],
      }),
    [chart, series],
  );
  return (
    <section className="min-w-0 rounded border border-line bg-panel p-2">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
        <strong className="text-text-soft">{series.label}</strong>
        <span className="text-text-dim">
          {series.points.length} 个采样点{series.status ? ` · ${series.status}` : ""}
        </span>
      </div>
      {series.points.length > 0 ? (
        <EChart
          resetRange={resetRange}
          interactionKey={JSON.stringify([
            interactionScope,
            measurementInteractionKey({ ...chart, series: [series] }),
          ])}
          interactionStore={interactionStore}
          ariaLabel={`${chart.title} · ${series.label}: ${chart.yLabel} by ${chart.xLabel}`}
          height={170}
          option={option}
          pointCount={series.points.length}
          seriesLabels={[series.label]}
          seriesCount={1}
        />
      ) : (
        <p className="m-0 grid h-[170px] place-items-center text-xs text-text-dim">
          {series.status ?? "此点不可用"}
        </p>
      )}
    </section>
  );
}

function entityChipClasses(selected: boolean): string {
  return [
    "rounded-full border px-2 py-1 text-xs font-semibold transition-colors",
    selected
      ? "border-blue/50 bg-blue-soft text-blue"
      : "border-line bg-panel text-text-dim hover:text-text-soft",
  ].join(" ");
}

function chartOptionLabel(chart: MeasurementChartPlan): string {
  if (chart.id.startsWith("slice:")) return chart.colorLabel ?? chart.yLabel;
  const color = chart.colorLabel ? ` · color: ${chart.colorLabel}` : "";
  const layout = chart.layout === "small-multiples" ? " · small multiples" : "";
  const fixed =
    chart.kind === "heatmap" && chart.fixedCoordinates.length > 0
      ? ` · fixed: ${fixedCoordinatesLabel(chart.fixedCoordinates)}`
      : "";
  return `${chart.title} — x: ${chart.xLabel} · y: ${chart.yLabel}${color}${layout}${fixed}`;
}

function fixedCoordinatesLabel(
  coordinates: Extract<MeasurementChartPlan, { kind: "heatmap" }>["fixedCoordinates"],
): string | undefined {
  if (coordinates.length === 0) return undefined;
  return coordinates
    .map(
      (coordinate) =>
        `${coordinate.label}=${fixedCoordinateValueLabel(coordinate)}${coordinate.value !== undefined && coordinate.unit ? ` ${coordinate.unit}` : ""}`,
    )
    .join(", ");
}

function fixedCoordinateValueLabel(
  coordinate: Extract<MeasurementChartPlan, { kind: "heatmap" }>["fixedCoordinates"][number],
): string {
  const value = coordinate.value;
  if (value === undefined) return `Index ${coordinate.index + 1}`;
  const rendered =
    typeof value === "boolean"
      ? value
        ? "true"
        : "false"
      : typeof value === "number"
        ? shortNumber(value)
        : typeof value === "string"
          ? JSON.stringify(value)
          : `${shortNumber(value.real)} ${value.imag < 0 ? "−" : "+"} ${shortNumber(Math.abs(value.imag))}i`;
  return coordinate.disambiguateIndex ? `${rendered} (Index ${coordinate.index! + 1})` : rendered;
}

function shortNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4 }).format(value);
}

function measurementInteractionKey(chart: MeasurementChartPlan): string {
  return JSON.stringify([
    chart.id,
    // Sparse/live and durable data can switch scatter/line without changing semantics.
    chart.kind === "line" || chart.kind === "scatter" ? "xy" : chart.kind,
    chart.xLabel,
    chart.yLabel,
    chart.colorLabel,
    chart.fixedCoordinates,
    chart.series.map((series) => [series.id, series.label]),
  ]);
}
