// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AcquisitionCurve, curveNumbers } from "./AcquisitionCurve";
import type {
  InstrumentAcquisition,
  InstrumentCollectReceipt,
  MeasurementAcquisitionValue,
} from "../../api-contract";

vi.mock("../../ui/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <pre data-testid="chart">{JSON.stringify(option)}</pre>
  ),
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
