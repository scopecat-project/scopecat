// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EChart } from "../../ui/EChart";
import { AcquisitionCurve, curveNumbers } from "./AcquisitionCurve";
import type {
  InstrumentAcquisition,
  InstrumentCollectReceipt,
  MeasurementAcquisitionValue,
} from "../../api-contract";

vi.mock("../../ui/EChart", () => ({
  EChart: vi.fn(({ option }: { option: unknown }) => (
    <pre data-testid="chart">{JSON.stringify(option)}</pre>
  )),
}));
afterEach(cleanup);
const response: MeasurementAcquisitionValue = {
  kind: "array",
  dtype: "complex128",
  shape: [2],
  unit: "ratio",
  values: [
    { real: 3, imag: 4 },
    { real: 0, imag: 2 },
  ],
};
const acquisition: InstrumentAcquisition = {
  id: "sweep",
  results: [
    {
      id: "frequency",
      dtype: "float64",
      role: "coordinate",
      axes: [{ id: "frequency", kind: "frequency", size: null, coordinate_result: "frequency" }],
    },
    {
      id: "response",
      dtype: "complex128",
      role: "observable",
      axes: [{ id: "frequency", kind: "frequency", size: null, coordinate_result: "frequency" }],
    },
  ],
};
const receipt: InstrumentCollectReceipt = {
  status: "collected",
  problems: [],
  readback: {
    values: {
      frequency: { kind: "array", dtype: "float64", unit: "Hz", shape: [2], values: [10, 30] },
      response,
    },
  },
};
it("uses returned coordinates rather than the first array as the plotted response", () => {
  render(<AcquisitionCurve acquisition={acquisition} receipt={receipt} />);
  expect(screen.getAllByTestId("chart")).toHaveLength(1);
  const option = JSON.parse(screen.getByTestId("chart").textContent);
  expect(option.grid).toMatchObject({ outerBoundsMode: "same", outerBoundsContain: "all" });
  expect(option.grid).not.toHaveProperty("containLabel");
  expect(option.xAxis).toMatchObject({
    nameTextStyle: { textMargin: 3 },
    axisLabel: { hideOverlap: true, textMargin: 3, margin: 12 },
  });
  expect(option.yAxis).toMatchObject({
    nameTextStyle: { textMargin: 3 },
    axisLabel: { hideOverlap: true, textMargin: 3 },
  });
  expect(screen.getByTestId("chart")).toHaveTextContent('"data":[[10,5],[30,2]]');
  fireEvent.change(screen.getByRole("combobox", { name: "复数表示" }), {
    target: { value: "imag" },
  });
  expect(screen.getByTestId("chart")).toHaveTextContent('"data":[[10,4],[30,2]]');
});
it("preserves invalid sample positions and rejects non-vector data", () => {
  expect(
    curveNumbers({ ...response, availability: { valid: [false, true], unavailable: [] } }, "real"),
  ).toEqual([null, 0]);
  expect(curveNumbers({ ...response, shape: [1, 2] }, "real")).toBeUndefined();
});
it("does not invent coordinates when the declared coordinate is missing", () => {
  render(
    <AcquisitionCurve
      acquisition={acquisition}
      receipt={{ ...receipt, readback: { values: { response } } }}
    />,
  );
  expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  expect(screen.getByText(/坐标不匹配/)).toBeVisible();
});

it("adapts only narrow chart labels while preserving full axis evidence and exact values", () => {
  const label = "s_parameter · very long measured transmission name with calibration context";
  const named = {
    ...acquisition,
    results: acquisition.results.map((result) =>
      result.id === "response" ? { ...result, label } : result,
    ),
  };
  const original = {
    ...receipt,
    readback: {
      values: {
        response,
        frequency: {
          kind: "array" as const,
          dtype: "float64" as const,
          unit: "Hz",
          shape: [2],
          values: [4_123_456_789, 5_987_654_321],
        },
      },
    },
  };
  render(<AcquisitionCurve acquisition={named} receipt={original} />);
  expect(
    screen.getByText(`纵轴 ${label} · 幅值 · ratio；横轴 frequency (Hz)；完整 2 点`),
  ).toBeVisible();
  const option = vi.mocked(EChart).mock.lastCall![0].option;
  const media = option.media as Array<{
    query?: { maxWidth: number };
    option: {
      xAxis: { name: string; axisLabel: { formatter?: (value: number) => string } };
      yAxis: { name: string; axisLabel: { formatter?: (value: number) => string } };
    };
  }>;
  expect(media[0]!.query).toEqual({ maxWidth: 420 });
  expect(media[0]!.option.xAxis.name).toBe("");
  expect(media[0]!.option.yAxis.name).toBe("");
  const format = media[0]!.option.xAxis.axisLabel.formatter!;
  expect([0, 1.23456, -1.2e-8, 4e9].map(format)).toEqual(["0", "1.23456", "-1.2e-8", "4e+9"]);
  expect([5e9, 5.0002e9, 5.001e9].map(format)).toEqual(["5e+9", "5.0002e+9", "5.001e+9"]);
  expect(media[0]!.option.yAxis.axisLabel.formatter).toBe(format);
  expect(media[1]!.query).toBeUndefined();
  expect(media[1]!.option.xAxis).toEqual({
    name: "frequency (Hz)",
    axisLabel: { formatter: undefined },
  });
  expect(media[1]!.option.yAxis).toEqual({
    name: `${label} · 幅值 · ratio`,
    axisLabel: { formatter: undefined },
  });
  expect(option.series).toEqual([
    {
      type: "line",
      name: `${label} · 幅值 · ratio`,
      data: [
        [4_123_456_789, 5],
        [5_987_654_321, 2],
      ],
      showSymbol: false,
      connectNulls: false,
    },
  ]);
  expect(option.tooltip).toEqual({ trigger: "axis" });
  fireEvent.change(screen.getByRole("combobox", { name: "复数表示" }), {
    target: { value: "phase" },
  });
  expect(
    screen.getByText(`纵轴 ${label} · 相位 (rad) · rad；横轴 frequency (Hz)；完整 2 点`),
  ).toBeVisible();
  expect(original.readback.values.frequency.values).toEqual([4_123_456_789, 5_987_654_321]);
  expect(original.readback.values.response).toEqual(response);
});
