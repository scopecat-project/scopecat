import { useState } from "react";
import type { InstrumentCollectReceipt, MeasurementAcquisitionValue } from "../../api-contract";
import { formControl, secondaryButton } from "../../ui/styles";

/** A bounded reading view with explicit decoder precision limits, separate from the technical receipt envelope. */
export function CollectResultData({ receipt }: { receipt: InstrumentCollectReceipt }) {
  const [open, setOpen] = useState(false);
  const values = receipt.readback?.values ?? {};
  const [selected, setSelected] = useState("");
  const id = selected in values ? selected : Object.keys(values)[0];
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="text-xs">结果数据（数值与可用性）</summary>
      {open && (
        <div className="grid min-w-0 gap-2 pt-2 text-xs">
          <label>
            结果字段{" "}
            <select
              className={formControl}
              aria-label="精确结果字段"
              value={id ?? ""}
              onChange={(event) => setSelected(event.target.value)}
            >
              {Object.keys(values).map((key) => (
                <option key={key} value={key}>
                  {key}
                </option>
              ))}
            </select>
          </label>
          {id ? <ResultValue key={id} value={values[id]!} /> : <p>没有返回值。</p>}
        </div>
      )}
    </details>
  );
}

function ResultValue({ value }: { value: MeasurementAcquisitionValue }) {
  const [page, setPage] = useState(0);
  const integerWarning =
    value.dtype === "int64" ? (
      <p className="text-yellow">
        int64 以浏览器数字解码，超出 ±9007199254740991 的整数可能已失去精度；这里的数值和 JSON
        诊断都不是这些大整数的精确原值。
      </p>
    ) : null;
  if (value.kind !== "array" || value.shape.length !== 1)
    return (
      <>
        {integerWarning}
        <pre className="max-h-72 overflow-auto">{JSON.stringify(value, null, 2)}</pre>
      </>
    );
  const last = Math.max(0, Math.ceil(value.values.length / 100) - 1);
  const current = Math.min(page, last);
  const offset = current * 100;
  return (
    <>
      {integerWarning}
      <p>
        {value.dtype} · 单位 {value.unit ?? "未知"} · 共 {value.values.length} 项。显示第{" "}
        {value.values.length ? offset + 1 : 0}–{Math.min(offset + 100, value.values.length)}{" "}
        项；浮点与复数不额外舍入，不转换为幅相。不可用位置的数值不是有效测量。
      </p>
      <div className="max-h-72 overflow-auto">
        <table className="w-full text-left">
          <thead>
            <tr>
              <th>原始下标</th>
              <th>解码数值</th>
              <th>可用性</th>
            </tr>
          </thead>
          <tbody>
            {value.values.slice(offset, offset + 100).map((sample, index) => (
              <tr key={offset + index}>
                <td>{offset + index}</td>
                <td>
                  {value.dtype === "int64" &&
                  typeof sample === "number" &&
                  !Number.isSafeInteger(sample)
                    ? `近似值 ${sample}（精度无法保证）`
                    : JSON.stringify(sample)}
                </td>
                <td>
                  {!value.availability || value.availability.valid[offset + index] === true
                    ? "有效"
                    : "不可用（原因见完整回执）"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          className={secondaryButton}
          disabled={current === 0}
          onClick={() => setPage(current - 1)}
        >
          上一页数据
        </button>
        <button
          type="button"
          className={secondaryButton}
          disabled={current === last}
          onClick={() => setPage(current + 1)}
        >
          下一页数据
        </button>
      </div>
    </>
  );
}
