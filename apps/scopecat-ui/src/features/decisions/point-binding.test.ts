import { analysisSeriesId } from "../runs/analysis-series-id";
import { analysisFigureOption } from "../runs/chart-options";
import { expect, it } from "vitest";
import type { AnalysisOutput } from "../../types";
import { pointBinding, bindingProblem, boundPointValue, type PointBinding } from "./point-binding";

const binding: PointBinding = {
  input: 1,
  output: "trace",
  layer: "measured",
  column: "frequency",
  field: "selected",
  unit: "MHz",
};
const structure = {
  type: "object",
  fields: { selected: { type: "union", variants: [{ type: "float" }, { type: "null" }] } },
};
const figure: AnalysisOutput = {
  id: "trace",
  title: "Not the field name",
  metadata: {},
  kind: "figure",
  content: {
    total_points: 2,
    truncated: false,
    layers: [
      {
        id: "measured",
        source: { kind: "dataset", output_id: "curve" },
        projection: { kind: "line", x: "frequency", y: "response" },
        total_points: 2,
        truncated: false,
        preview: {
          kind: "line",
          x_axis: { label: "Unrelated label", unit: "MHz" },
          y_axis: { label: "Value" },
          series: [{ id: "signal", x: [6499, 6500], y: [0.2, 1] }],
        },
      },
    ],
  },
};

it("requires an explicit numeric target and unit, never inferring labels", () => {
  expect(pointBinding({}, structure)).toBeUndefined();
  expect(pointBinding({ point_binding: binding }, structure)).toEqual(binding);
  for (const change of [
    { unit: undefined },
    { input: -1 },
    { input: 0.5 },
    { field: "frequency" },
    { column: "" },
  ]) {
    expect(pointBinding({ point_binding: { ...binding, ...change } }, structure)).toBeUndefined();
  }
  expect(
    pointBinding(
      { point_binding: binding },
      { type: "object", fields: { selected: { type: "string" } } },
    ),
  ).toBeUndefined();
});

it("reads the declared retained layer and column, rejecting incompatible/stale selections", () => {
  const point = { seriesId: analysisSeriesId("measured", "signal"), dataIndex: 1 };
  expect(boundPointValue(binding, figure, point)).toBe(6500);
  for (const change of [
    { output: "old" },
    { layer: "fit" },
    { column: "Unrelated label" },
    { unit: "GHz" },
  ]) {
    expect(bindingProblem({ ...binding, ...change }, figure)).toBeDefined();
    expect(boundPointValue({ ...binding, ...change }, figure, point)).toBeUndefined();
  }
  for (const invalid of [
    { ...point, seriesId: "fit/signal" },
    { ...point, dataIndex: -1 },
    { ...point, dataIndex: 2 },
    { ...point, dataIndex: 0.5 },
  ]) {
    expect(boundPointValue(binding, figure, invalid)).toBeUndefined();
  }
  expect(boundPointValue({ ...binding, column: "response", unit: null }, figure, point)).toBe(1);
});

it("keeps slash-containing layer and series identities distinct", () => {
  if (figure.kind !== "figure") throw new Error("fixture");
  const layer = figure.content.layers[0]!;
  const content = {
    ...figure.content,
    layers: [
      { ...layer, id: "a/b", preview: { ...layer.preview, series: [{ id: "c", x: [1], y: [2] }] } },
      { ...layer, id: "a", preview: { ...layer.preview, series: [{ id: "b/c", x: [9], y: [8] }] } },
    ],
  };
  const output = { ...figure, content };
  const target = { ...binding, layer: "a/b" };
  const first = analysisSeriesId("a/b", "c");
  const other = analysisSeriesId("a", "b/c");
  expect(first).not.toBe(other);
  expect(analysisSeriesId("a", "b/c", "uncertainty")).not.toBe(first);
  expect(analysisFigureOption(content).series).toMatchObject([{ id: first }, { id: other }]);
  expect(boundPointValue(target, output, { seriesId: first, dataIndex: 0 })).toBe(1);
  expect(boundPointValue(target, output, { seriesId: other, dataIndex: 0 })).toBeUndefined();
});
