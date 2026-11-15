// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentAcquisition } from "../../api-contract";
import { EChart } from "../../ui/EChart";
import { BoundedMeasurement } from "./BoundedMeasurement";
import { useMeasurementPreview, type MeasurementPreview } from "./measurement-preview";
vi.mock("../../ui/EChart", () => ({ EChart: vi.fn(() => <div data-testid="canvas" />) }));
vi.mock("./measurement-preview", async (original) => ({
  ...(await original<typeof import("./measurement-preview")>()),
  useMeasurementPreview: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const acquisition: InstrumentAcquisition = {
  id: "capture",
  results: [
    { id: "frequency", role: "coordinate", dtype: "float64", unit: "Hz" },
    ...["s21", "s11"].map((id) => ({
      id,
      role: "observable" as const,
      dtype: "complex128" as const,
      unit: "ratio",
      axes: [
        {
          id: "f",
          kind: "frequency" as const,
          coordinate_result: "frequency",
          unit: "Hz",
          size: 2,
        },
      ],
    })),
  ],
};
function setup(partial = false) {
  const data: MeasurementPreview = {
    method: "exact_slice",
    status: "collected",
    raw_bytes: 100,
    metadata: {},
    metadata_truncated: false,
    fields_truncated: false,
    problems: [],
    problems_truncated: false,
    fields: acquisition.results.map((result) => ({
      request_id: result.id,
      kind: "array",
      dtype: result.dtype,
      unit: result.unit ?? null,
      unit_truncated: false,
      shape: [partial ? 4 : 2],
      total_count: partial ? 4 : 2,
      start: 0,
      returned_count: 2,
      next_start: partial ? 2 : null,
      complete: !partial,
      values:
        result.id === "frequency"
          ? [5e9, 6e9]
          : [
              { real: 3, imag: 4 },
              { real: 0, imag: 1 },
            ],
      truncated_indices: [],
      metadata: {},
      metadata_truncated: false,
    })),
  };
  vi.mocked(useMeasurementPreview).mockReturnValue({
    data,
    error: undefined,
    isPending: false,
    retry: vi.fn(),
  });
  return render(
    <BoundedMeasurement workspace source={{ recordId: "workspace" }} acquisition={acquisition} />,
  );
}
it("uses one fill-height field, retains coordinates and switches without stacking plots", () => {
  setup();
  expect(screen.getAllByTestId("canvas")).toHaveLength(1);
  expect(vi.mocked(EChart).mock.calls.at(-1)![0]).toMatchObject({
    height: "fill",
    seriesLabels: ["s21"],
    option: {
      series: [
        {
          data: [
            [5e9, 5],
            [6e9, 1],
          ],
        },
      ],
    },
  });
  expect(vi.mocked(useMeasurementPreview).mock.calls.at(-1)![2]).toEqual(["s21", "frequency"]);
  expect(screen.queryByLabelText("局部窗口导航")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("曲线字段"), { target: { value: "s11" } });
  expect(screen.getAllByTestId("canvas")).toHaveLength(1);
  expect(vi.mocked(EChart).mock.calls.at(-1)![0]).toMatchObject({
    height: "fill",
    seriesLabels: ["s11"],
  });
  expect(vi.mocked(useMeasurementPreview).mock.calls.at(-1)![2]).toEqual(["s11", "frequency"]);
});
it("keeps partial-window navigation and explicitly distinguishes it from a full overview", () => {
  setup(true);
  expect(screen.getByLabelText("局部窗口导航")).toBeVisible();
  expect(screen.getByText(/局部原始数据窗口，非全量概览/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "下一页数据" }));
  expect(vi.mocked(useMeasurementPreview).mock.calls.at(-1)![1]).toBe(2);
});
it("opens independently scrolling data details without unmounting the selected plot", () => {
  setup();
  const canvas = screen.getByTestId("canvas");
  fireEvent.click(screen.getByRole("button", { name: "数据详情" }));
  expect(screen.getByRole("region", { name: "数据详情" })).toHaveStyle({
    overflow: "auto",
    minHeight: "0",
  });
  expect(canvas).toBeInTheDocument();
  expect(canvas).not.toBeVisible();
  const details = screen.getByText("查看局部原始数值（按窗口）").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  fireEvent.change(screen.getByLabelText("精确结果字段"), { target: { value: "frequency" } });
  expect(screen.getByLabelText("曲线字段")).toHaveValue("s21");
  fireEvent.click(screen.getByRole("button", { name: "关闭数据详情" }));
  expect(screen.getByTestId("canvas")).toBe(canvas);
  expect(canvas).toBeVisible();
});

it("falls back to an available measured field instead of retaining an obsolete selection", () => {
  setup();
  cleanup();
  render(
    <BoundedMeasurement
      workspace
      source={{ recordId: "changed-contract" }}
      acquisition={acquisition}
      displayKey="retained"
      displayStore={
        new Map([
          [
            "retained",
            { start: 0, representation: "magnitude", plotField: "removed", zoom: new Map() },
          ],
        ])
      }
    />,
  );
  expect(screen.getByLabelText("曲线字段")).toHaveValue("s21");
  expect(vi.mocked(useMeasurementPreview).mock.calls.at(-1)![2]).toEqual(["s21", "frequency"]);
});
it("keeps untyped results inspectable without inventing a curve", () => {
  setup();
  cleanup();
  render(<BoundedMeasurement workspace source={{ recordId: "no-contract" }} />);
  expect(screen.queryByTestId("canvas")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("没有可绘制的字段契约");
  fireEvent.click(screen.getByRole("button", { name: "数据详情" }));
  expect(screen.getByRole("region", { name: "数据详情" })).toBeVisible();
});

it("pages long exact-value fields independently of a short plotted field and restores both windows", () => {
  const longResult = {
    id: "long-array",
    role: "observable" as const,
    dtype: "float64" as const,
    unit: "V",
  };
  const contract = { ...acquisition, results: [...acquisition.results, longResult] };
  vi.mocked(useMeasurementPreview).mockImplementation((_source, start, ids) => ({
    isPending: false,
    error: undefined,
    retry: vi.fn(),
    data: {
      method: "exact_slice",
      status: "collected",
      raw_bytes: 10000,
      metadata: {},
      metadata_truncated: false,
      fields_truncated: false,
      problems: [],
      problems_truncated: false,
      fields: contract.results
        .filter((result) => !ids || ids.includes(result.id))
        .map((result) => {
          const total = result.id === "long-array" ? 1025 : 2;
          const count = Math.max(0, Math.min(512, total - start));
          return {
            request_id: result.id,
            kind: "array",
            dtype: result.dtype,
            unit: result.unit ?? null,
            unit_truncated: false,
            shape: [total],
            total_count: total,
            start,
            returned_count: count,
            next_start: start + count < total ? start + count : null,
            complete: start === 0 && count === total,
            values: Array.from({ length: count }, (_, i) =>
              result.dtype === "complex128" ? { real: start + i, imag: 0 } : start + i,
            ),
            truncated_indices: [],
            metadata: {},
            metadata_truncated: false,
          };
        }),
    },
  }));
  render(
    <BoundedMeasurement workspace source={{ recordId: "mixed-lengths" }} acquisition={contract} />,
  );
  expect(screen.queryByLabelText("局部窗口导航")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "数据详情" }));
  const details = screen.getByText("查看局部原始数值（按窗口）").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  fireEvent.change(screen.getByLabelText("精确结果字段"), { target: { value: "long-array" } });
  expect(screen.getByLabelText("局部窗口导航")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "下一页数据" }));
  expect(screen.getByLabelText("预览起始下标")).toHaveValue(512);
  expect(vi.mocked(useMeasurementPreview).mock.calls.at(-1)!.slice(1)).toEqual([
    512,
    ["long-array"],
  ]);
  fireEvent.click(screen.getByRole("button", { name: "下一页数据" }));
  expect(screen.getByLabelText("预览起始下标")).toHaveValue(1024);
  expect(screen.getByRole("button", { name: "下一页数据" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "上一页数据" }));
  expect(screen.getByLabelText("预览起始下标")).toHaveValue(512);
  fireEvent.click(screen.getByRole("button", { name: "关闭数据详情" }));
  expect(screen.queryByLabelText("局部窗口导航")).not.toBeInTheDocument();
  expect(vi.mocked(useMeasurementPreview).mock.calls.at(-1)!.slice(1)).toEqual([
    0,
    ["s21", "frequency"],
  ]);
  expect(vi.mocked(EChart).mock.calls.at(-1)![0]).toMatchObject({
    pointCount: 2,
    seriesLabels: ["s21"],
  });
  fireEvent.click(screen.getByRole("button", { name: "数据详情" }));
  expect(screen.getByLabelText("预览起始下标")).toHaveValue(512);
  expect(screen.getByLabelText("精确结果字段")).toHaveValue("long-array");
});

it("allows leaving details while a newly selected exact-value request is pending", () => {
  setup();
  const loaded = vi.mocked(useMeasurementPreview).mock.results.at(-1)!.value;
  vi.mocked(useMeasurementPreview).mockImplementation((_source, _start, ids) =>
    ids?.[0] === "frequency"
      ? { data: undefined, error: undefined, isPending: true, retry: vi.fn() }
      : loaded,
  );
  fireEvent.click(screen.getByRole("button", { name: "数据详情" }));
  const details = screen.getByText("查看局部原始数值（按窗口）").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  fireEvent.change(screen.getByLabelText("精确结果字段"), { target: { value: "frequency" } });
  expect(screen.getByRole("status")).toHaveTextContent("正在读取局部结果");
  const close = screen.getByRole("button", { name: "关闭数据详情" });
  expect(close).toBeEnabled();
  fireEvent.click(close);
  expect(screen.getByTestId("canvas")).toBeVisible();
});
