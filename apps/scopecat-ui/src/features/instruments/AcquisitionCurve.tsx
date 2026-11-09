import { useState } from "react";
import type {
  InstrumentAcquisition,
  InstrumentCollectReceipt,
  MeasurementAcquisitionValue,
} from "../../api-contract";
import { EChart } from "../../ui/EChart";

type Representation = "magnitude" | "phase" | "real" | "imag";
const labels: Record<Representation, string> = {
  magnitude: "幅值",
  phase: "相位 (rad)",
  real: "实部",
  imag: "虚部",
};

export function curveNumbers(
  value: MeasurementAcquisitionValue,
  representation: Representation,
): (number | null)[] | undefined {
  if (value.kind !== "array" || value.shape.length !== 1) return undefined;
  return value.values.map((sample, index) => {
    if (value.availability && value.availability.valid[index] !== true) return null;
    if (typeof sample === "number") return Number.isFinite(sample) ? sample : null;
    if (typeof sample !== "object" || sample === null || !("real" in sample) || !("imag" in sample))
      return null;
    if (
      typeof sample.real !== "number" ||
      typeof sample.imag !== "number" ||
      !Number.isFinite(sample.real) ||
      !Number.isFinite(sample.imag)
    )
      return null;
    switch (representation) {
      case "real":
        return sample.real;
      case "imag":
        return sample.imag;
      case "phase":
        return Math.atan2(sample.imag, sample.real);
      case "magnitude":
        return Math.hypot(sample.real, sample.imag);
    }
  });
}

export function AcquisitionCurve({
  receipt,
  acquisition,
}: {
  receipt: InstrumentCollectReceipt;
  acquisition: InstrumentAcquisition;
}) {
  const [representation, setRepresentation] = useState<Representation>("magnitude");
  const values = receipt.readback?.values ?? {};
  return (
    <div className="grid min-w-0 gap-3">
      <label className="text-xs">
        复数表示{" "}
        <select
          aria-label="复数表示"
          value={representation}
          onChange={(event) => setRepresentation(event.target.value as Representation)}
        >
          {Object.entries(labels).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {acquisition.results
        .filter((result) => result.role !== "coordinate")
        .map((result) => {
          const value = values[result.id];
          if (!value) return null;
          const y = curveNumbers(value, representation);
          const axis = result.axes?.length === 1 ? result.axes[0] : undefined;
          const coordinateId = axis?.coordinate_result;
          const coordinate = coordinateId ? values[coordinateId] : undefined;
          const x = coordinate ? curveNumbers(coordinate, "real") : undefined;
          const xLabel = coordinateId
            ? `${coordinateId} (${coordinate?.kind === "array" ? (coordinate.unit ?? "单位未知") : "未知"})`
            : "样本序号（无坐标证据）";
          const unit =
            value.kind === "array" ? (value.unit ?? "单位未知") : (result.unit ?? "单位未知");
          const yLabel = `${result.label ?? result.id} · ${value.kind === "array" && value.dtype === "complex128" ? labels[representation] + " · " : ""}${representation === "phase" && value.kind === "array" && value.dtype === "complex128" ? "rad" : unit}`;
          if (!y || (coordinateId && (!x || x.length !== y.length)))
            return (
              <p key={result.id} className="text-xs">
                {result.id}：无法绘制，结果维度或坐标不匹配。原始回执保留如下。
              </p>
            );
          const data = y.map((sample, index) => [x ? x[index] : index, sample]);
          return (
            <section key={result.id} className="min-w-0">
              <p className="text-xs [overflow-wrap:anywhere]">
                {yLabel}；横轴 {xLabel}；完整 {y.length} 点
              </p>
              <EChart
                ariaLabel={`${result.id} 完整曲线`}
                height={260}
                pointCount={y.length}
                seriesCount={1}
                seriesLabels={[yLabel]}
                option={{
                  animation: false,
                  tooltip: { trigger: "axis" },
                  // ECharts 6 fits axis names and tick labels inside these bounds.
                  grid: {
                    left: 10,
                    right: 15,
                    top: 20,
                    bottom: 55,
                    outerBoundsMode: "same",
                    outerBoundsContain: "all",
                  },
                  xAxis: { type: "value", name: xLabel, scale: true },
                  yAxis: { type: "value", name: yLabel, scale: true },
                  dataZoom: [{ type: "inside" }, { type: "slider" }],
                  series: [
                    { type: "line", name: yLabel, data, showSymbol: false, connectNulls: false },
                  ],
                }}
              />
            </section>
          );
        })}
      <CollectReceiptDetails receipt={receipt} />
    </div>
  );
}

export function CollectReceiptDetails({ receipt }: { receipt: InstrumentCollectReceipt }) {
  const [showReceipt, setShowReceipt] = useState(false);
  return (
    <details onToggle={(event) => setShowReceipt(event.currentTarget.open)}>
      <summary className="text-xs">完整回执与采集证据（含复数原值）</summary>
      <pre className="max-h-80 overflow-auto text-xs">
        {showReceipt ? JSON.stringify(receipt, null, 2) : ""}
      </pre>
    </details>
  );
}
