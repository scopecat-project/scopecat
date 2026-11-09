import { useState } from "react";
import { useQueries } from "@tanstack/react-query";
import type { InstrumentAcquisition, InstrumentCollectReceipt } from "../../api-contract";
import { ApiError } from "../../api-client";
import { EChart } from "../../ui/EChart";
import { curveNumbers } from "./AcquisitionCurve";
import { decodeCollectReceipt } from "./hardware-receipt-wire";
import type { SavedMeasurement } from "./direct-measurement-api";

export const COMPARISON_LIMIT = 4;

/** Missing historical identity must never merge otherwise unrelated records. */
export function measurementGroupKey(record: SavedMeasurement): string {
  const { instrument_id, series_id, line_id } = record.selection;
  return JSON.stringify([
    instrument_id,
    series_id,
    line_id,
    !series_id || !line_id ? record.id : null,
  ]);
}

export function measurementReceiptQuery(record: SavedMeasurement) {
  return {
    queryKey: ["direct-measurement-receipt", record.id],
    queryFn: async () => {
      const response = await fetch(
        `/api/v1/direct-measurements/${encodeURIComponent(record.id)}/receipt`,
      );
      if (!response.ok) throw new ApiError("保存的回执读取失败", response.status);
      return decodeCollectReceipt(await response.arrayBuffer());
    },
  };
}

type Representation = "magnitude" | "phase" | "real" | "imag";
type Entry = { record: SavedMeasurement; acquisition: InstrumentAcquisition | undefined };
type Curve = { recordId: string; label: string; data: (number | null)[][] };
type Group = { key: string; resultId: string; xLabel: string; yLabel: string; curves: Curve[] };

/** Stable serialization avoids treating object property insertion order as evidence. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  return JSON.stringify(value) ?? "null";
}

/** Explicit public interface family, not a result-name heuristic. Only the two
 * known operations share NetworkSweepResults. Axis generation recipes differ,
 * so compare their complete result contracts except size/coordinates: the
 * actual coordinate arrays remain authoritative and are never synthesized. */
function knownNetworkResults(record: SavedMeasurement, acquisition: InstrumentAcquisition) {
  const known =
    (record.intent.interface_id === "scopecat.network_sweep/v1" &&
      record.intent.acquisition_id === "sweep") ||
    (record.intent.interface_id === "scopecat.existing_network_trace/v1" &&
      record.intent.acquisition_id === "read_trace");
  if (!known || acquisition.id !== record.intent.acquisition_id || acquisition.results.length !== 2)
    return undefined;
  const frequency = acquisition.results.find((result) => result.id === "frequency");
  const signal = acquisition.results.find((result) => result.id === "s_parameter");
  if (
    frequency?.role !== "coordinate" ||
    frequency.dtype !== "float64" ||
    frequency.unit !== "Hz" ||
    signal?.role !== "observable" ||
    signal.dtype !== "complex128" ||
    signal.unit !== "ratio"
  )
    return undefined;
  if (
    !acquisition.results.every(
      (result) =>
        result.axes?.length === 1 &&
        result.axes[0]!.id === "frequency" &&
        result.axes[0]!.kind === "frequency" &&
        result.axes[0]!.unit === "Hz" &&
        result.axes[0]!.coordinate_result === "frequency",
    )
  )
    return undefined;
  return acquisition.results.map((result) => ({
    ...result,
    axes: result.axes!.map((axis) => ({ ...axis, size: null, coordinates: null })),
  }));
}

export function comparisonGroups(
  entries: (Entry & { receipt?: InstrumentCollectReceipt })[],
  representation: Representation,
) {
  const groups = new Map<string, Group>();
  const excluded: string[] = [];
  for (const { record, acquisition, receipt } of entries) {
    if (!receipt) continue;
    if (!acquisition) {
      excluded.push(`${record.id}：采集契约或组件路径无法解析。`);
      continue;
    }
    for (const result of acquisition.results.filter((item) => item.role !== "coordinate")) {
      const reason = `${record.id} / ${result.id}`;
      const values = receipt.readback?.values ?? {};
      const value = values[result.id];
      const axis = result.axes?.length === 1 ? result.axes[0] : undefined;
      const coordinateId = axis?.coordinate_result;
      const coordinateContract = acquisition.results.find(
        (item) => item.id === coordinateId && item.role === "coordinate",
      );
      const coordinate = coordinateId ? values[coordinateId] : undefined;
      const y = value && curveNumbers(value, representation);
      const x = coordinate && curveNumbers(coordinate, "real");
      if (
        !y ||
        !x ||
        x.length !== y.length ||
        value?.kind !== "array" ||
        coordinate?.kind !== "array" ||
        coordinate.dtype === "complex128" ||
        value.shape[0] !== y.length ||
        coordinate.shape[0] !== x.length ||
        value.dtype !== result.dtype ||
        coordinate.dtype !== coordinateContract?.dtype ||
        (result.unit != null && result.unit !== value.unit) ||
        (coordinateContract?.unit != null && coordinateContract.unit !== coordinate.unit) ||
        !coordinateContract ||
        !axis?.kind ||
        !value.unit ||
        !coordinate.unit ||
        !x.some((sample, index) => sample !== null && y[index] !== null)
      ) {
        excluded.push(`${reason}：缺少可用的一维数值曲线、真实坐标或明确单位，保留单独视图。`);
        continue;
      }
      // Same names alone are not evidence: require the exact instrument, implementation,
      // interface, component, acquisition/result contract and receipt units/dtypes.
      const networkResults = knownNetworkResults(record, acquisition);
      const key = canonical([
        record.selection.instrument_id,
        record.intent.instrument_id,
        record.description.instrument_id,
        record.description.implementation_id,
        record.description.implementation_version,
        record.intent.component_path ?? [],
        networkResults
          ? ["known-network-trace/v1", networkResults, result.id]
          : [record.intent.interface_id, acquisition, result, coordinateContract],
        coordinate.unit,
        value.unit,
        coordinate.dtype,
        value.dtype,
      ]);
      const yUnit = value.dtype === "complex128" && representation === "phase" ? "rad" : value.unit;
      const group = groups.get(key) ?? {
        key,
        resultId: result.id,
        xLabel: `${axis.kind} · ${coordinateId} (${coordinate.unit})`,
        yLabel: `${result.label ?? result.id} (${yUnit})`,
        curves: [],
      };
      group.curves.push({
        recordId: record.id,
        label: `${record.id} · ${record.selection.line_label || "未标注测量对象"} · ${new Date(record.collected_at).toLocaleString()}`,
        data: y.map((sample, index) => [x[index] ?? null, sample]),
      });
      groups.set(key, group);
    }
  }
  return {
    groups: [...groups.values()].filter((group) => group.curves.length > 1),
    excluded: [
      ...excluded,
      ...[...groups.values()]
        .filter((group) => group.curves.length === 1)
        .map(
          (group) =>
            `${group.curves[0]!.recordId} / ${group.resultId}：没有设备、接口、组件、采集契约及坐标/数值单位均兼容的另一条曲线，保留单独视图。`,
        ),
    ],
  };
}

function conditionEvidence(
  record: SavedMeasurement,
  receipt?: InstrumentCollectReceipt,
): Record<string, unknown> {
  return {
    设备: [
      record.selection.instrument_id,
      record.description.implementation_id,
      record.description.implementation_version,
    ],
    接口: record.intent.interface_id,
    组件: record.intent.component_path ?? [],
    采集来源: [record.intent.acquisition_id, record.intent.result_ids ?? []],
    "系列/线路": [record.selection.series_id, record.selection.line_id],
    设置快照: record.setup,
    采集前观测: record.observed_before_collection,
    回执生成证据: [receipt?.metadata ?? null, receipt?.readback?.metadata ?? null],
  };
}

export function conditionDifferences(
  record: SavedMeasurement,
  baseline: SavedMeasurement,
  receipt?: InstrumentCollectReceipt,
  baselineReceipt?: InstrumentCollectReceipt,
): string[] {
  const before = conditionEvidence(baseline, baselineReceipt);
  return Object.entries(conditionEvidence(record, receipt))
    .filter(([key, value]) => canonical(value) !== canonical(before[key]))
    .map(([key]) => key);
}

export function MeasurementComparison({ entries }: { entries: Entry[] }) {
  const [representation, setRepresentation] = useState<Representation>("magnitude");
  const receipts = useQueries({
    queries: entries.map(({ record }) => measurementReceiptQuery(record)),
  });
  const { groups, excluded } = comparisonGroups(
    entries.map((entry, index) => ({ ...entry, receipt: receipts[index]?.data })),
    representation,
  );
  return (
    <section
      aria-label="同轴比较"
      className="grid min-w-0 gap-3 border-t border-line pt-2 [overflow-wrap:anywhere]"
    >
      <h4 className="text-sm font-semibold">同轴比较</h4>
      <p className="text-xs text-text-dim">
        仅叠加同设备、组件和兼容采集契约且坐标/数值单位一致的曲线。仅已知 network_sweep/sweep 与
        existing_network_trace/read_trace
        可跨接口叠加。同轴仅用于观察，不证明物理等价；线路或设置可能不同。每条曲线保留真实坐标，不插值、不重采样、不做差。
      </p>
      <label className="text-xs">
        比较复数表示{" "}
        <select
          aria-label="比较复数表示"
          value={representation}
          onChange={(event) => setRepresentation(event.target.value as Representation)}
        >
          <option value="magnitude">幅值</option>
          <option value="phase">相位 (rad)</option>
          <option value="real">实部</option>
          <option value="imag">虚部</option>
        </select>
      </label>
      {receipts.some((receipt) => receipt.isPending) && <p role="status">正在读取比较回执…</p>}
      {receipts.map(
        (receipt, index) =>
          receipt.error && (
            <p key={entries[index]!.record.id} role="alert">
              {entries[index]!.record.id}：{receipt.error.message}；其他记录仍可查看。
            </p>
          ),
      )}
      <div className="grid gap-2 text-xs">
        {entries.map(({ record }, index) => (
          <details key={record.id}>
            <summary>
              {record.id} · 条件与证据
              {index > 0
                ? `（与首条差异：${conditionDifferences(record, entries[0]!.record, receipts[index]?.data, receipts[0]?.data).join("、") || "已记录字段未见差异，不代表条件相同"}）`
                : "（参考记录）"}
            </summary>
            <p>
              设备：{record.selection.instrument_id}；接口：{record.intent.interface_id}；组件：
              {record.intent.component_path?.join(" / ") || "根组件"}；采集来源：
              {record.intent.acquisition_id}；系列：{record.selection.series_id || "未知"}；线路：
              {record.selection.line_id || "未知"}
            </p>
            <p>
              设置快照与采集前观测不保证是生成曲线时的设置；请核对回执生成证据。缺失证据保持未知。
            </p>
            <pre className="max-h-72 overflow-auto">
              {JSON.stringify(
                {
                  setup: record.setup,
                  observed_before_collection: record.observed_before_collection,
                  intent: record.intent,
                  receipt_evidence: receipts[index]?.data
                    ? {
                        status: receipts[index].data.status,
                        problems: receipts[index].data.problems,
                        metadata: receipts[index].data.metadata ?? null,
                        readback_metadata: receipts[index].data.readback?.metadata ?? null,
                      }
                    : "尚未读取",
                },
                null,
                2,
              )}
            </pre>
          </details>
        ))}
      </div>
      {groups.map((group) => (
        <EChart
          key={group.key}
          ariaLabel={`${group.resultId} 同轴比较`}
          height={320}
          pointCount={group.curves.reduce((count, curve) => count + curve.data.length, 0)}
          seriesCount={group.curves.length}
          seriesLabels={group.curves.map((curve) => curve.label)}
          option={{
            animation: false,
            tooltip: { trigger: "item" },
            legend: { type: "scroll", top: 0 },
            grid: {
              outerBoundsMode: "same",
              outerBoundsContain: "all",
              left: 15,
              right: 25,
              top: 60,
              bottom: 65,
            },
            xAxis: { type: "value", name: group.xLabel, nameMoveOverlap: true, scale: true },
            yAxis: { type: "value", name: group.yLabel, nameMoveOverlap: true, scale: true },
            dataZoom: [{ type: "inside" }, { type: "slider" }],
            series: group.curves.map((curve) => ({
              type: "line",
              id: curve.recordId,
              name: curve.label,
              data: curve.data,
              showSymbol: false,
              connectNulls: false,
            })),
          }}
        />
      ))}
      {excluded.map((reason) => (
        <p className="text-xs" key={reason}>
          {reason}
        </p>
      ))}
      {!groups.length && !receipts.some((receipt) => receipt.isPending) && (
        <p className="text-xs">当前没有可同轴叠加的曲线。下方保留每条记录的独立视图与原始回执。</p>
      )}
    </section>
  );
}
