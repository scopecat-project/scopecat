// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { MeasurementRecord, MeasurementDatasetSchema } from "../../api-contract";
import { measurementShotMeanCharts, measurementTraceQueryPlans } from "./measurement-visualization";
import { MeasurementDataPreview } from "./MeasurementDataPreview";
import { traceSchema } from "./measurement-trace.test-support";
vi.mock("../../ui/EChartRuntime", () => ({ EChartRuntime: () => null }));
afterEach(cleanup);
function schema(): MeasurementDatasetSchema {
  const base = traceSchema();
  return {
    ...base,
    dimensions: [
      { id: "point", kind: "point", size: 2 },
      { id: "sample", kind: "shot", size: 2 },
    ],
    variables: base.variables?.filter((item) => item.id !== "frequency"),
    primary_coordinates: ["bias"],
  };
}
function record(point: number, real: number[]): MeasurementRecord {
  return {
    run_id: "teaching",
    point_index: point,
    logical_point_id: String(point),
    coordinates: { bias: { kind: "scalar", dtype: "float64", unit: "V", value: point } },
    observables: {
      response: {
        kind: "array",
        dtype: "complex128",
        unit: "ratio",
        shape: [real.length],
        values: real.map((value) => ({ real: value, imag: 0 })),
      },
    },
  };
}
it("averages complex shots before magnitude and keeps scan points separate", () => {
  const records = [record(0, [-2, 2]), record(1, [4, 8])];
  const original = JSON.stringify(records);
  const charts = measurementShotMeanCharts(records, schema());
  expect(charts[0]?.series[0]?.points).toEqual([
    { x: 0, y: 0 },
    { x: 1, y: 6 },
  ]);
  expect(charts[0]?.note).toContain("每点 2 shots");
  expect(charts[0]?.note).toContain("不平均扫描轴");
  expect(JSON.stringify(records)).toBe(original);
});
it("does not infer shots from a name or reduce multiple local axes", () => {
  const source = schema();
  expect(
    measurementShotMeanCharts([record(0, [1, 2])], {
      ...source,
      dimensions: source.dimensions.map((axis) => ({
        ...axis,
        kind: axis.kind === "shot" ? "sample" : axis.kind,
      })),
    }),
  ).toEqual([]);
  expect(
    measurementShotMeanCharts([record(0, [1, 2])], {
      ...source,
      variables: source.variables?.map((value) =>
        value.role === "observable" ? { ...value, dims: ["point", "qubit", "sample"] } : value,
      ),
    }),
  ).toEqual([]);
});
it("omits incomplete or unavailable shots without silently averaging their subset", () => {
  const bad = record(1, [3, 5]);
  const array = bad.observables.response!;
  if (array.kind !== "array") throw new Error("fixture");
  array.availability = {
    valid: [true, false],
    unavailable: [{ reason: "missing", flat_indices: [1] }],
  };
  const charts = measurementShotMeanCharts([record(0, [1, 3]), bad, record(2, [10])], schema());
  expect(charts[0]?.series[0]?.points).toEqual([{ x: 0, y: 2 }]);
  expect(charts[0]?.note).toContain("1/3");
});
it("defaults to a mean view with the original trace available on request", () => {
  const source = schema();
  render(
    <MeasurementDataPreview
      preview={{ schema: source, items: [record(0, [1, 3])] }}
      sliceError={null}
      slicePending={false}
      fixedAxisIndices={{}}
      onFixedAxisIndexChange={vi.fn()}
      tracePlans={measurementTraceQueryPlans(source)}
    />,
  );
  expect(screen.getByRole("img", { name: /shot 平均/ })).toBeVisible();
  const raw = screen.getByText("原始轨迹").closest("details")!;
  expect(raw).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText("原始轨迹"));
  expect(screen.getByLabelText("Measurement trace")).toBeInTheDocument();
});

it("never computes a mean from a summarized or zero-length array", () => {
  const source = record(0, [1, 3]);
  expect(
    measurementShotMeanCharts(
      [
        {
          ...source,
          logical_point_id: source.logical_point_id ?? null,
          observables: {
            response: {
              kind: "array_summary",
              dtype: "complex128",
              unit: "ratio",
              shape: [2],
              sample_count: 2,
              available_sample_count: 2,
              unavailable_reasons: [],
            },
          },
        },
      ],
      schema(),
    ),
  ).toEqual([]);
  expect(measurementShotMeanCharts([record(0, [])], schema())).toEqual([]);
});

it("preserves raw expansion and selected mean mode when complete data temporarily disappears", () => {
  const source = schema();
  const props = {
    sliceError: null,
    slicePending: false,
    fixedAxisIndices: {},
    onFixedAxisIndexChange: vi.fn(),
    tracePlans: measurementTraceQueryPlans(source),
  };
  const view = render(
    <MeasurementDataPreview {...props} preview={{ schema: source, items: [record(0, [1, 3])] }} />,
  );
  fireEvent.click(screen.getByText("原始轨迹"));
  fireEvent.change(screen.getByLabelText("测量图表"), {
    target: { value: measurementShotMeanCharts([record(0, [1, 3])], source)[2]!.id },
  });
  const choice = measurementShotMeanCharts([record(0, [1, 3])], source)[2]!.id;
  expect(screen.getByRole("img", { name: /shot 平均 real/ })).toBeInTheDocument();
  view.rerender(<MeasurementDataPreview {...props} preview={{ schema: source, items: [] }} />);
  expect(screen.getByText("原始轨迹").closest("details")).toHaveAttribute("open");
  view.rerender(
    <MeasurementDataPreview {...props} preview={{ schema: source, items: [record(0, [1, 3])] }} />,
  );
  expect(screen.getByText("原始轨迹").closest("details")).toHaveAttribute("open");
  expect(screen.getByRole("img", { name: /shot 平均 real/ })).toBeInTheDocument();
  expect(screen.getByLabelText("测量图表")).toHaveValue(choice);
});
