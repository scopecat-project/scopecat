import { lastMeasurementWindowOffset } from "./measurement-window";
// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  MeasurementDatasetSchema,
  MeasurementRecord,
  MeasurementValue,
} from "../../api-contract";
import { EChartRuntime } from "../../ui/EChartRuntime";
import * as visualization from "./measurement-visualization";
import { MeasurementWorkspace, MeasurementInteractionBoundary } from "./MeasurementWorkspace";
import { MeasurementDataPreview } from "./MeasurementDataPreview";
import {
  measurementEntityAxes,
  measurementSliceAxisValue,
  measurementSlicePlan,
  measurementTable,
  measurementTraceQueryPlans,
  planMeasurementCharts,
  type MeasurementSliceAxis,
} from "./measurement-visualization";
import { tracePreview, traceSeries } from "./measurement-trace.test-support";
import type { components } from "../../api-schema";

vi.mock("../../ui/EChartRuntime", () => ({ EChartRuntime: vi.fn(() => null) }));

afterEach(cleanup);

describe("measurement visualization", () => {
  it("selects axes independently without expanding the Cartesian product", () => {
    const schema = threeDimensionalGridSchema();
    const selection = { xAxisId: "bias", yAxisId: "row" };
    expect(measurementSlicePlan(schema, selection)).toMatchObject({
      varyingAxes: [{ id: "bias" }, { id: "row" }],
      fixedAxes: [{ id: "column" }],
      variableIds: ["row", "column", "bias", "temperature"],
    });
    const records = slicedGridRecords().filter(
      (item) => item.coordinates.column?.kind === "scalar" && item.coordinates.column.value === 2,
    );
    const charts = planMeasurementCharts(records, schema, { column: 1 }, {}, selection);
    expect(charts).toHaveLength(1);
    expect(charts[0]).toMatchObject({
      kind: "heatmap",
      xLabel: "Bias [V]",
      yLabel: "Row [mm]",
      fixedCoordinates: [{ id: "column", value: 2 }],
    });
    const swapped = planMeasurementCharts(
      records,
      schema,
      { column: 1 },
      {},
      { xAxisId: "row", yAxisId: "bias" },
    );
    expect(swapped[0]?.id).toBe(charts[0]?.id);
    expect(swapped[0]).toMatchObject({ xLabel: "Row [mm]", yLabel: "Bias [V]" });
    expect(
      measurementSlicePlan(schema, { xAxisId: "bias", yAxisId: "bias" })?.fixedAxes.map(
        (axis) => axis.id,
      ),
    ).toEqual(["row", "column"]);
    expect(
      measurementSlicePlan(schema, { xAxisId: "gone" })?.varyingAxes.map((axis) => axis.id),
    ).toEqual(["row"]);
  });

  it("keeps missing, repeated and irregular values as observations without filling a grid", () => {
    const schema = threeDimensionalGridSchema();
    const selection = { xAxisId: "row", yAxisId: "column" };
    const records = slicedGridRecords([0]);
    const missing = {
      ...records[1]!,
      observables: {
        temperature: {
          kind: "unavailable" as const,
          dtype: "float64" as const,
          shape: [],
          unit: "K",
          metadata: {},
          reason: "missing" as const,
        },
      },
    };
    const charts = planMeasurementCharts(
      [records[0]!, missing, ...records.slice(3)],
      schema,
      { bias: 0 },
      {},
      selection,
    );
    expect(charts[0]).toMatchObject({ kind: "color-scatter" });
    expect(charts[0]?.series[0]?.points).toHaveLength(4);
    expect(charts[0]?.series[0]?.points.every((point) => point.color !== 0)).toBe(true);
    const repeated = planMeasurementCharts(
      [...records, records[0]!],
      schema,
      { bias: 0 },
      {},
      selection,
    );
    expect(repeated[0]?.kind).toBe("color-scatter");
    expect(repeated[0]?.series[0]?.points).toHaveLength(7);
    expect(planMeasurementCharts([], schema, { bias: 0 }, {}, selection)).toEqual([]);
  });

  it("plots only the selected horizontal coordinate and does not bridge absent samples", () => {
    const schema = threeDimensionalGridSchema();
    const records = slicedGridRecords([0]).filter((item) => item.point_index < 6);
    const selection = { xAxisId: "column" };
    expect(measurementSlicePlan(schema, selection)?.fixedAxes.map((axis) => axis.id)).toEqual([
      "row",
      "bias",
    ]);
    const full = planMeasurementCharts(records, schema, { row: 0, bias: 0 }, {}, selection);
    expect(full[0]).toMatchObject({ kind: "line", xLabel: "Column [mm]" });
    const partial = planMeasurementCharts(
      [records[0]!, records[2]!],
      schema,
      { row: 0, bias: 0 },
      {},
      selection,
    );
    expect(partial[0]?.kind).toBe("scatter");
    expect(partial[0]?.series[0]?.points.map((point) => point.x)).toEqual([1, 3]);
  });

  it("retains an unavailable signal and never substitutes point index for a missing selected coordinate", () => {
    const schema = threeDimensionalGridSchema();
    const items = slicedGridRecords([0])
      .slice(0, 3)
      .map((item) => ({
        ...item,
        coordinates: {},
        observables: {
          temperature: {
            kind: "unavailable" as const,
            dtype: "float64" as const,
            shape: [],
            unit: "K",
            metadata: {},
            reason: "missing" as const,
          },
        },
      }));
    for (const selection of [{ xAxisId: "column" }, { xAxisId: "row", yAxisId: "column" }]) {
      const charts = planMeasurementCharts(items, schema, { row: 0, bias: 0 }, {}, selection);
      expect(charts).toHaveLength(1);
      expect(charts[0]?.series[0]?.points).toEqual([]);
      expect(charts[0]?.xLabel).not.toBe("Point index");
    }
  });

  it("keeps entity scalar views on the selected coordinate when that coordinate is unavailable", () => {
    const schema = entityScalarSchema();
    const charts = planMeasurementCharts(
      [record(0, {}, { readout: entityArray([1, 2], "ratio") })],
      schema,
      {},
      {},
      { xAxisId: "bias" },
    );
    expect(charts).toHaveLength(1);
    expect(charts[0]?.xLabel).toBe("Bias [V]");
    expect(charts[0]?.series.every((series) => series.points.length === 0)).toBe(true);
  });

  it("keeps a chosen signal through an empty or pending slice", () => {
    const schema = twoDimensionalGridSchema("complex128");
    const items = slicedGridRecords([0]).map((item) => ({
      ...item,
      observables: { temperature: complexScalar(1, 2, "ratio") },
    }));
    const props = {
      preview: { schema, items, truncated: false },
      sliceError: null,
      fixedAxisIndices: {},
      measurementAxes: { xAxisId: "row", yAxisId: "column" },
      onFixedAxisIndexChange: vi.fn(),
    };
    const { rerender } = render(
      <MeasurementDataPreview
        {...props}
        slicePending={false}
        slice={slicePreview(schema, items)}
      />,
    );
    const phase = "slice:temperature:phase";
    fireEvent.change(screen.getByLabelText("测量图表"), { target: { value: phase } });
    rerender(<MeasurementDataPreview {...props} slicePending={true} />);
    expect(screen.queryByLabelText("测量图表")).not.toBeInTheDocument();
    rerender(
      <MeasurementDataPreview
        {...props}
        slicePending={false}
        slice={slicePreview(schema, items)}
      />,
    );
    expect(screen.getByLabelText("测量图表")).toHaveValue(phase);
  });

  it("offers axis choices, independent stepping and a readable current slice", () => {
    const schema = threeDimensionalGridSchema();
    const onAxes = vi.fn();
    const onFixed = vi.fn();
    render(
      <MeasurementDataPreview
        preview={{ schema, items: slicedGridRecords([0]), truncated: false }}
        slice={slicePreview(schema, slicedGridRecords([0]))}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        measurementAxes={{ xAxisId: "row", yAxisId: "column" }}
        onMeasurementAxesChange={onAxes}
        onFixedAxisIndexChange={onFixed}
      />,
    );
    expect(screen.getByTestId("measurement-slice-summary")).toHaveTextContent("Bias = 0 V");
    expect(screen.getByRole("button", { name: "Previous Bias value" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next Bias value" }));
    expect(onFixed).toHaveBeenCalledWith("bias", 1);
    fireEvent.change(screen.getByLabelText("Horizontal axis"), { target: { value: "column" } });
    expect(onAxes).toHaveBeenLastCalledWith({ xAxisId: "column", yAxisId: undefined });
    fireEvent.change(screen.getByLabelText("Vertical axis"), { target: { value: "" } });
    expect(onAxes).toHaveBeenLastCalledWith({ xAxisId: "row", yAxisId: undefined });
  });

  it("labels array summaries without interpreting them as absent or numeric data", () => {
    const item: components["schemas"]["MeasurementRecordPreview"] = {
      run_id: "run",
      point_index: 0,
      logical_point_id: null,
      coordinates: { bias: scalar(0, "V") },
      observables: {
        readout: {
          kind: "array_summary",
          dtype: "float64",
          unit: "ratio",
          shape: [2],
          sample_count: 2,
          available_sample_count: 1,
          unavailable_reasons: ["missing"],
        },
      },
    };
    const table = measurementTable([item], entityScalarSchema());
    expect(table.rows[0]!.cells[2]).toBe(
      "2 samples ratio · 1/2 available · missing · preview summary",
    );
    expect(planMeasurementCharts([item], entityScalarSchema())).toEqual([]);
  });

  it("plans a labeled scalar line from point coordinates", () => {
    const schema = scalarSchema();
    const items = [0, 1, 2].map((point) =>
      record(point, { bias: scalar(point * 0.1, "V") }, { signal: scalar(point + 1, "ratio") }),
    );

    expect(planMeasurementCharts(items, schema)).toEqual([
      expect.objectContaining({
        kind: "line",
        title: "Response",
        xLabel: "Bias [V]",
        yLabel: "Response [ratio]",
        series: [
          expect.objectContaining({
            points: [
              { x: 0, y: 1 },
              { x: 0.1, y: 2 },
              { x: 0.2, y: 3 },
            ],
          }),
        ],
      }),
    ]);
    expect(measurementTable(items, schema).rows[1]?.cells).toEqual(["1", "0.1 V", "2 ratio"]);
  });

  it("does not fill missing persisted value units from the schema", () => {
    const items = [
      record(
        0,
        { bias: { kind: "scalar", dtype: "float64", value: 0.1 } },
        { signal: { kind: "scalar", dtype: "float64", value: 2 } },
      ),
    ];

    expect(measurementTable(items, scalarSchema()).rows[0]?.cells).toEqual(["0", "0.1", "2"]);
  });

  it("retains exact entity choices across unavailable content and isolates another run", () => {
    const schema = entityScalarSchema();
    const items = [record(0, { bias: scalar(0, "V") }, { readout: entityArray([1, 11], "ratio") })];
    const content = (
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />
    );
    const view = render(
      <MeasurementInteractionBoundary key="run-a">{content}</MeasurementInteractionBoundary>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Qubit Q1" }));
    expect(screen.getByRole("button", { name: "Qubit Q1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    view.rerender(
      <MeasurementInteractionBoundary key="run-a">
        <p role="alert">synthetic read error</p>
      </MeasurementInteractionBoundary>,
    );
    expect(screen.queryByRole("button", { name: "Qubit Q1" })).not.toBeInTheDocument();
    view.rerender(
      <MeasurementInteractionBoundary key="run-a">{content}</MeasurementInteractionBoundary>,
    );
    expect(screen.getByRole("button", { name: "Qubit Q1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    view.rerender(
      <MeasurementInteractionBoundary key="run-b">{content}</MeasurementInteractionBoundary>,
    );
    expect(screen.getByRole("button", { name: "Qubit Q1" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("compares entity scalar values by default and filters them with identity chips", async () => {
    const schema = entityScalarSchema();
    const items = [0, 1, 2].map((point) =>
      record(
        point,
        { bias: scalar(point * 0.1, "V") },
        { readout: entityArray([point + 1, point + 11], "ratio") },
      ),
    );

    expect(measurementEntityAxes(schema)).toEqual([
      {
        id: "qubit",
        label: "Qubit",
        size: 2,
        members: [
          { id: "q0", identity: '["qubit","q0"]', index: 0, kind: "qubit", label: "Q0" },
          { id: "q1", identity: '["qubit","q1"]', index: 1, kind: "qubit", label: "Q1" },
        ],
      },
    ]);
    expect(measurementSlicePlan(schema)?.variableIds).toEqual(["bias", "readout"]);
    expect(measurementTraceQueryPlans(schema)).toEqual([]);
    expect(planMeasurementCharts(items, schema)).toEqual([
      expect.objectContaining({
        id: "entity-scalar:readout:qubit:bias:value",
        kind: "line",
        title: "Readout by Qubit",
        series: [
          expect.objectContaining({
            id: 'readout:["qubit","q0"]',
            label: "Q0",
            points: [
              { x: 0, y: 1 },
              { x: 0.1, y: 2 },
              { x: 0.2, y: 3 },
            ],
          }),
          expect.objectContaining({ id: 'readout:["qubit","q1"]', label: "Q1" }),
        ],
      }),
    ]);
    expect(measurementTable(items, schema).rows[0]?.cells.at(-1)).toBe(
      "2 entities · ratio · All available",
    );
    expect(measurementTable(items, schema, { qubit: [1] }).rows[0]?.cells.at(-1)).toBe(
      "1/2 entities · ratio · All available",
    );

    const view = render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("measurement-entity-controls")).toBeVisible();
    expect(screen.getByRole("button", { name: "All 2" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Qubit Q1" }));

    expect(screen.getByRole("button", { name: "Qubit Q1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByText(/Record table/));
    expect(await screen.findAllByText("1/2 entities · ratio · All available")).toHaveLength(3);
    expect(
      screen.getByRole("img", { name: "Readout by Qubit: Readout [ratio] by Bias [V]" }),
    ).toHaveAccessibleDescription(/系列：Q1/);

    const reorderedSchema: MeasurementDatasetSchema = {
      ...schema,
      dimensions: [
        schema.dimensions[0]!,
        {
          ...schema.dimensions[1]!,
          index: {
            kind: "entity",
            values: [
              schema.dimensions[1]!.index!.values[1]!,
              schema.dimensions[1]!.index!.values[0]!,
            ],
          },
        },
      ],
    };
    const reorderedItems = items.map((item) => {
      const values = (item.observables.readout as Extract<MeasurementValue, { kind: "array" }>)
        .values as number[];
      return {
        ...item,
        observables: { readout: entityArray([values[1]!, values[0]!], "ratio") },
      };
    });
    view.rerender(
      <MeasurementDataPreview
        preview={{ schema: reorderedSchema, items: reorderedItems }}
        slice={slicePreview(reorderedSchema, reorderedItems)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Qubit Q1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("img", { name: "Readout by Qubit: Readout [ratio] by Bias [V]" }),
    ).toHaveAccessibleDescription(/系列：Q1/);
  });

  it("keeps mixed-kind entity labels and selection identities collision-free", () => {
    const schema: MeasurementDatasetSchema = {
      ...baseSchema(),
      dimensions: [
        { id: "point", kind: "point", size: 1 },
        {
          id: "subject",
          kind: "entity",
          size: 2,
          index: {
            kind: "entity",
            values: [
              { id: "q0", kind: "qubit" },
              { id: "q0", kind: "coupler" },
            ],
          },
        },
      ],
    };

    expect(measurementEntityAxes(schema)[0]?.members).toEqual([
      {
        id: "q0",
        identity: '["qubit","q0"]',
        index: 0,
        kind: "qubit",
        label: "qubit:q0",
      },
      {
        id: "q0",
        identity: '["coupler","q0"]',
        index: 1,
        kind: "coupler",
        label: "coupler:q0",
      },
    ]);
  });

  it("delegates same-shape entity waveforms to bounded server traces", () => {
    const schema = entityTraceSchema(false);
    const items = [
      record(
        0,
        {
          frequency: entityMatrix(
            [
              [4, 5, 6],
              [4, 5, 6],
            ],
            "GHz",
          ),
        },
        {
          trace: entityMatrix(
            [
              [1, 2, 3],
              [4, 0, 6],
            ],
            "ratio",
            [
              [true, true, true],
              [true, false, true],
            ],
          ),
        },
      ),
    ];

    expect(planMeasurementCharts(items, schema)).toEqual([]);
    expect(measurementTraceQueryPlans(schema)[0]).toMatchObject({
      id: "trace:trace:frequency:value",
      entityAxisId: "qubit",
    });
    expect(measurementTable(items, schema).rows[0]?.cells.at(-1)).toBe(
      "2 entities · 3 samples each · ratio · 1/2 complete · missing",
    );
  });

  it("reports dense array failures only for the selected entities", () => {
    const source = entityTraceSchema(false);
    const schema = {
      ...source,
      variables: source.variables?.map((variable) => ({
        ...variable,
        dims: ["point", "sample", "qubit"],
      })),
    };
    const items = [
      record(
        0,
        {
          frequency: {
            kind: "array",
            dtype: "float64",
            unit: "GHz",
            shape: [3, 2],
            values: [
              [4, 4],
              [5, 5],
              [6, 6],
            ],
          },
        },
        {
          trace: {
            kind: "array",
            dtype: "float64",
            unit: "ratio",
            shape: [3, 2],
            values: [
              [1, 4],
              [2, 5],
              [3, 6],
            ],
            availability: {
              valid: [
                [false, true],
                [true, false],
                [true, true],
              ],
              unavailable: [
                { reason: "missing", flat_indices: [0] },
                { reason: "overload", flat_indices: [3] },
              ],
            },
          },
        },
      ),
    ];

    expect(measurementTable(items, schema, { qubit: [0] }).rows[0]?.cells.at(-1)).toBe(
      "1/2 entities · 3 samples each · ratio · 0/1 complete · missing",
    );
    expect(measurementTable(items, schema, { qubit: [1] }).rows[0]?.cells.at(-1)).toBe(
      "1/2 entities · 3 samples each · ratio · 0/1 complete · overload",
    );
  });

  it("keeps a persisted multi-selection visible when an entity axis grows", () => {
    const smallSchema = entityScalarSchemaWithEntityCount(3);
    const largeSchema = entityScalarSchemaWithEntityCount(13);
    const common = {
      sliceError: null,
      slicePending: false,
      fixedAxisIndices: {},
      onFixedAxisIndexChange: vi.fn(),
    };
    const view = render(
      <MeasurementDataPreview {...common} preview={{ schema: smallSchema, items: [] }} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Qubit Q0" }));
    fireEvent.click(screen.getByRole("button", { name: "Qubit Q1" }));
    expect(screen.getByText("2 selected")).toBeVisible();

    view.rerender(
      <MeasurementDataPreview {...common} preview={{ schema: largeSchema, items: [] }} />,
    );

    const selector = screen.getByRole("combobox", { name: "Qubit entity" });
    expect(selector).toHaveValue("__multiple_entities__");
    expect(screen.getByRole("option", { name: "2 selected" })).toBeDisabled();
    expect(screen.getByText("2 selected", { selector: "span" })).toBeVisible();
  });

  it("delegates segmented entity arrays to an indexed bounded trace", () => {
    const schema = entityTraceSchema(true);
    const items = [
      record(
        0,
        {},
        {
          trace: {
            kind: "segmented_array",
            dtype: "float64",
            unit: "V",
            segments: [
              {
                kind: "array",
                dtype: "float64",
                unit: "V",
                shape: [2],
                values: [1, 2],
              },
              {
                kind: "unavailable",
                reason: "missing",
                dtype: "float64",
                unit: "V",
                shape: [null],
                metadata: { entity: "q1" },
              },
            ],
          },
        },
      ),
    ];

    expect(planMeasurementCharts(items, schema)).toEqual([]);
    const tracePlan = measurementTraceQueryPlans(schema)[0];
    expect(tracePlan).toMatchObject({
      id: "trace:trace:sample:value",
      entityAxisId: "qubit",
    });
    expect(tracePlan).not.toHaveProperty("coordinateId");
    expect(measurementTable(items, schema).rows[0]?.cells.at(-1)).toBe(
      "2 entities · variable length · V · 1/2 complete · missing",
    );
  });

  it("always uses scatter for a one-dimensional point cloud", () => {
    const schema: MeasurementDatasetSchema = {
      ...scalarSchema(),
      point_domain: { kind: "point_cloud", columns: [{ id: "bias" }] },
    };
    const items = [0, 1, 2].map((point) =>
      record(point, { bias: scalar(point, "V") }, { signal: scalar(point + 1, "ratio") }),
    );

    expect(planMeasurementCharts(items, schema)).toEqual([
      expect.objectContaining({
        kind: "scatter",
        xLabel: "Bias [V]",
        yLabel: "Response [ratio]",
      }),
    ]);
  });

  it("uses ordered point-cloud columns for a two-dimensional color scatter", () => {
    const schema = twoDimensionalPointCloudSchema();
    const items = [
      record(0, { x: scalar(0, "mm"), y: scalar(2, "mm") }, { temperature: scalar(10, "K") }),
      record(1, { x: scalar(1, "mm"), y: scalar(0, "mm") }, { temperature: scalar(20, "K") }),
      record(2, { x: scalar(2, "mm"), y: scalar(1, "mm") }, { temperature: scalar(30, "K") }),
    ];

    const [chart] = planMeasurementCharts(items, schema);
    expect(chart).toMatchObject({
      kind: "color-scatter",
      title: "Temperature map",
      xLabel: "X [mm]",
      yLabel: "Y [mm]",
      colorLabel: "Temperature [K]",
      series: [
        {
          points: [
            { x: 0, y: 2, color: 10 },
            { x: 1, y: 0, color: 20 },
            { x: 2, y: 1, color: 30 },
          ],
        },
      ],
    });

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("img", {
        name: "Temperature map: Y [mm] by X [mm], colored by Temperature [K]",
      }),
    ).toBeVisible();
  });

  it("uses authored product-grid axis order and sizes for a rectangular 2x3 heatmap", () => {
    const schema = twoDimensionalGridSchema();
    const items = gridRecords((point) => scalar(point + 10, "K"));

    const [chart] = planMeasurementCharts(items, schema);
    expect(chart).toMatchObject({
      id: "heatmap:temperature:row:column:value",
      kind: "heatmap",
      title: "Temperature heatmap",
      xLabel: "Row [mm]",
      yLabel: "Column [mm]",
      colorLabel: "Temperature [K]",
      grid: { xValues: [10, 20], yValues: [1, 2, 3] },
      note: "X and Y follow the authored product-grid axis order.",
    });
    expect(chart?.series[0]?.points).toEqual([
      { x: 10, y: 1, color: 10 },
      { x: 10, y: 2, color: 11 },
      { x: 10, y: 3, color: 12 },
      { x: 20, y: 1, color: 13 },
      { x: 20, y: 2, color: 14 },
      { x: 20, y: 3, color: 15 },
    ]);

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("img", {
        name: "Temperature heatmap: Column [mm] by Row [mm], colored by Temperature [K]",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("option", {
        name: "Temperature heatmap — x: Row [mm] · y: Column [mm] · color: Temperature [K]",
      }),
    ).toHaveValue("heatmap:temperature:row:column:value");
  });

  it("offers every complex mode as a product-grid heatmap", () => {
    const schema = twoDimensionalGridSchema("complex128");
    const items = gridRecords((point) => complexScalar(point + 3, point + 4, "ratio"));

    const heatmaps = planMeasurementCharts(items, schema).filter(
      (chart) => chart.kind === "heatmap",
    );

    expect(heatmaps.map((chart) => chart.title)).toEqual([
      "Temperature magnitude heatmap",
      "Temperature phase heatmap",
      "Temperature real heatmap",
      "Temperature imaginary heatmap",
    ]);
    expect(heatmaps.map((chart) => chart.colorLabel)).toEqual([
      "|Temperature| [ratio]",
      "phase(Temperature) [rad]",
      "Re(Temperature) [ratio]",
      "Im(Temperature) [ratio]",
    ]);
    expect(heatmaps[0]?.series[0]?.points[0]?.color).toBe(5);
    expect(heatmaps[1]?.series[0]?.points[0]?.color).toBeCloseTo(Math.atan2(4, 3));
    expect(heatmaps[2]?.series[0]?.points[0]?.color).toBe(3);
    expect(heatmaps[3]?.series[0]?.points[0]?.color).toBe(4);
  });

  it("skips heatmaps for missing or duplicate product-grid cells", () => {
    const schema = twoDimensionalGridSchema();
    const complete = gridRecords((point) => scalar(point + 10, "K"));
    const missing = complete.slice(0, -1);
    const duplicate = [...missing, complete[4]!];

    for (const records of [missing, duplicate]) {
      const charts = planMeasurementCharts(records, schema);
      expect(charts.some((chart) => chart.kind === "heatmap")).toBe(false);
      expect(charts[0]).toMatchObject({ kind: "scatter", title: "Temperature" });
    }
  });

  it("renders one requested high-dimensional heatmap slice", () => {
    const schema = threeDimensionalGridSchema();
    const items = slicedGridRecords();
    const selected = slicedGridRecords([0]);

    const heatmaps = planMeasurementCharts(selected, schema, { bias: 0 }).filter(
      (chart) => chart.kind === "heatmap",
    );

    expect(heatmaps).toEqual([
      expect.objectContaining({
        id: "heatmap:temperature:row:column:value:fixed:bias=0%40index%3A0",
        xLabel: "Row [mm]",
        yLabel: "Column [mm]",
        fixedCoordinates: [
          expect.objectContaining({ id: "bias", label: "Bias", unit: "V", value: 0, index: 0 }),
        ],
        grid: { xValues: [10, 20], yValues: [1, 2, 3] },
      }),
    ]);
    expect(heatmaps[0]?.series[0]?.points.map((point) => point.color)).toEqual([
      10, 11, 12, 13, 14, 15,
    ]);

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, selected)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("option", {
        name: "Temperature heatmap — x: Row [mm] · y: Column [mm] · color: Temperature [K] · fixed: Bias=0 V",
      }),
    ).toHaveValue("heatmap:temperature:row:column:value:fixed:bias=0%40index%3A0");
    expect(
      screen.getByRole("img", {
        name: "Temperature heatmap: Column [mm] by Row [mm], colored by Temperature [K], fixed at Bias=0 V",
      }),
    ).toBeVisible();
  });

  it("plans one high-dimensional semantic slice using canonical axis values", () => {
    const schema = threeDimensionalGridSchema();
    const items = slicedGridRecords();
    const selected = slicedGridRecords([0]);
    const onFixedAxisIndexChange = vi.fn();

    expect(measurementSlicePlan(schema)).toMatchObject({
      varyingAxes: [{ id: "row" }, { id: "column" }],
      fixedAxes: [
        {
          id: "bias",
          size: 2,
          source: {
            kind: "values",
            values: [scalar(0, "V"), scalar(1, "V")],
          },
        },
      ],
      variableIds: ["row", "column", "temperature"],
      heatmap: {
        xAxis: { id: "row" },
        yAxis: { id: "column" },
        observableIds: ["temperature"],
      },
    });

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, selected)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={onFixedAxisIndexChange}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "Bias slice" });
    expect(selector).toHaveValue("0");
    expect(screen.getByRole("option", { name: "0 V" })).toBeVisible();
    expect(screen.getByRole("option", { name: "1 V" })).toBeVisible();
    expect(screen.getByText("6 of 6 slice points durable")).toBeVisible();
    fireEvent.change(selector, { target: { value: "1" } });
    expect(onFixedAxisIndexChange).toHaveBeenCalledWith("bias", 1);
  });

  it("reads compact range and linear axes without materializing them", () => {
    const rangeAxis: MeasurementSliceAxis = {
      id: "bias",
      label: "Bias",
      size: 3,
      source: {
        kind: "range",
        start: scalar(0, "V"),
        stop: scalar(1, "V"),
      },
    };
    const linearAxis: MeasurementSliceAxis = {
      id: "frequency",
      label: "Frequency",
      size: 3,
      source: {
        kind: "linear",
        center: scalar(10, "GHz"),
        span: scalar(4, "GHz"),
      },
    };

    expect([0, 1, 2].map((index) => measurementSliceAxisValue(rangeAxis, index))).toEqual([
      scalar(0, "V"),
      scalar(0.5, "V"),
      scalar(1, "V"),
    ]);
    expect([0, 1, 2].map((index) => measurementSliceAxisValue(linearAxis, index))).toEqual([
      scalar(8, "GHz"),
      scalar(10, "GHz"),
      scalar(12, "GHz"),
    ]);
  });

  it("plans a one-dimensional product-grid slice without requiring a heatmap", () => {
    const plan = measurementSlicePlan(scalarSchema());

    expect(plan).toMatchObject({
      varyingAxes: [{ id: "bias", size: 3 }],
      fixedAxes: [],
      variableIds: ["bias", "signal"],
    });
    expect(plan?.heatmap).toBeUndefined();
  });

  it("keeps fixed-axis selection and a bounded server trace for a ragged trace-only grid", () => {
    const schema = traceOnlyRaggedGridSchema();
    const previewItems = traceOnlyRaggedGridRecords();
    const selectedItems = traceOnlyRaggedGridRecords([0]).map((item) => ({
      ...item,
      coordinates: {
        column: item.coordinates.column!,
        row: item.coordinates.row!,
      },
      observables: {},
    }));
    const plan = measurementSlicePlan(schema);
    const tracePlans = measurementTraceQueryPlans(schema);
    const boundedTrace = tracePreview({
      observable_id: "spectrum",
      observable_label: "Spectrum",
      value_mode: "value",
      series: [traceSeries(0, [4, 5, 6], [1, 2, 3])],
      selected_series_count: 6,
      returned_series_count: 1,
      source_sample_count: 3,
      returned_sample_count: 3,
    });

    expect(plan).toMatchObject({
      varyingAxes: [{ id: "row" }, { id: "column" }],
      fixedAxes: [{ id: "bias", size: 2 }],
      variableIds: ["row", "column"],
    });
    expect(plan?.heatmap).toBeUndefined();

    render(
      <MeasurementDataPreview
        preview={{ schema, items: previewItems }}
        slice={slicePreview(schema, selectedItems)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={vi.fn()}
        tracePlans={tracePlans}
        selectedTracePlanId={tracePlans[0]!.id}
        tracePreview={boundedTrace}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Bias slice" })).toBeVisible();
    expect(
      screen.getByRole("img", { name: "Spectrum: Spectrum [ratio] by Frequency [GHz]" }),
    ).toBeVisible();
  });

  it("uses the first authored opaque axis when no numeric domain axis exists", () => {
    const schema = opaqueOnlyGridSchema();
    const plan = measurementSlicePlan(schema);

    expect(plan).toMatchObject({
      varyingAxes: [{ id: "opaque", source: { kind: "values", values: [null, null] } }],
      fixedAxes: [
        {
          id: "device",
          source: {
            kind: "values",
            values: [stringScalar("q1"), stringScalar("q2")],
          },
        },
      ],
      variableIds: [],
    });
    expect(plan?.heatmap).toBeUndefined();

    render(
      <MeasurementDataPreview
        preview={{ schema, items: [] }}
        slice={slicePreview(schema, [])}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ device: 0 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Device slice" })).toBeVisible();
    expect(screen.getByRole("option", { name: "q1" })).toBeVisible();
    expect(screen.getByRole("option", { name: "q2" })).toBeVisible();
  });

  it("rejects empty product-grid domains and zero-sized axes", () => {
    expect(measurementSlicePlan(baseSchema())).toBeUndefined();
    expect(
      measurementSlicePlan({
        ...baseSchema(),
        dimensions: [{ id: "point", kind: "point", size: 0 }],
        point_domain: { kind: "product_grid", axes: [gridAxis("empty", [])] },
      }),
    ).toBeUndefined();
  });

  it("selects opaque fixed axes by authored index", () => {
    const schema = opaqueSlicedGridSchema();
    const items = gridRecords((point) => scalar(point + 10, "K"));

    expect(measurementSlicePlan(schema)).toMatchObject({
      varyingAxes: [{ id: "row" }, { id: "column" }],
      fixedAxes: [{ id: "opaque", source: { kind: "values", values: [null, null] } }],
      variableIds: ["row", "column", "temperature"],
      heatmap: {
        xAxis: { id: "row" },
        yAxis: { id: "column" },
        observableIds: ["temperature"],
      },
    });

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ opaque: 1 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("option", { name: "Index 1" })).toBeVisible();
    expect(screen.getByRole("option", { name: "Index 2" })).toBeVisible();
    expect(
      screen.getByRole("img", {
        name: "Temperature heatmap: Column [mm] by Row [mm], colored by Temperature [K], fixed at Opaque=Index 2",
      }),
    ).toBeVisible();
  });

  it("keeps a server trace beside a semantic heatmap slice", () => {
    const schema = mixedGridSchema();
    const previewItems = mixedGridRecords();
    const selectedItems = mixedGridRecords([0]).map((item) => ({
      ...item,
      coordinates: { column: item.coordinates.column!, row: item.coordinates.row! },
      observables: { temperature: item.observables.temperature! },
    }));
    const tracePlans = measurementTraceQueryPlans(schema);
    const boundedTrace = tracePreview({
      observable_id: "spectrum",
      observable_label: "Spectrum",
      value_mode: "value",
      series: [traceSeries(0, [4, 5, 6], [1, 2, 3])],
      selected_series_count: 6,
      returned_series_count: 1,
      source_sample_count: 3,
      returned_sample_count: 3,
    });

    render(
      <MeasurementDataPreview
        preview={{ schema, items: previewItems }}
        slice={slicePreview(schema, selectedItems)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={vi.fn()}
        tracePlans={tracePlans}
        selectedTracePlanId={tracePlans[0]!.id}
        tracePreview={boundedTrace}
      />,
    );

    expect(
      screen.getByRole("option", {
        name: /Temperature heatmap — x: Row \[mm\] · y: Column \[mm\]/,
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("option", {
        name: "Spectrum",
      }),
    ).toBeVisible();
  });

  it("disambiguates duplicate fixed-axis values with authored indices", () => {
    const schema = duplicateFixedGridSchema();
    const items = slicedGridRecords([0]).map((item) => ({
      ...item,
      logical_point_id: `point-${item.point_index + 1}`,
      point_index: item.point_index + 1,
    }));

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 1 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("option", { name: "0 V · Index 1" })).toBeVisible();
    expect(screen.getByRole("option", { name: "0 V · Index 2" })).toBeVisible();
    expect(
      screen.getByRole("img", {
        name: "Temperature heatmap: Column [mm] by Row [mm], colored by Temperature [K], fixed at Bias=0 (Index 2) V",
      }),
    ).toBeVisible();
  });

  it("uses an index input instead of rendering thousands of slice options", () => {
    const schema = largeFixedGridSchema();
    const items = slicedGridRecords([0]);

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("spinbutton", { name: "Bias slice index" })).toHaveValue(1);
    expect(screen.queryByRole("combobox", { name: "Bias slice" })).not.toBeInTheDocument();
    expect(screen.getByText("0 V · 300 values")).toBeVisible();
  });

  it("lets a large-axis position be cleared and typed before committing a bounded index", () => {
    const schema = largeFixedGridSchema();
    const onCommit = vi.fn();
    function Preview() {
      const [index, setIndex] = useState(0);
      return (
        <MeasurementDataPreview
          preview={{ schema, items: [] }}
          sliceError={null}
          slicePending={false}
          fixedAxisIndices={{ bias: index }}
          onFixedAxisIndexChange={(_axis, next) => {
            onCommit(next);
            setIndex(next);
          }}
        />
      );
    }
    render(<Preview />);
    let input = screen.getByRole("spinbutton", { name: "Bias slice index" });
    fireEvent.change(input, { target: { value: "" } });
    expect(input).toHaveValue(null);
    for (const value of ["1", "10", "100"]) {
      fireEvent.change(input, { target: { value } });
      expect(input).toHaveValue(Number(value));
    }
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCommit).toHaveBeenLastCalledWith(99);
    input = screen.getByRole("spinbutton", { name: "Bias slice index" });
    expect(input).toHaveValue(100);
    fireEvent.change(input, { target: { value: "999" } });
    expect(input).toHaveValue(999);
    fireEvent.blur(input);
    expect(input).toHaveValue(100);
    expect(onCommit).toHaveBeenCalledTimes(1);
    fireEvent.change(input, { target: { value: "20" } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenLastCalledWith(19);
    input = screen.getByRole("spinbutton", { name: "Bias slice index" });
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).toHaveValue(20);
  });

  it("explains that a live selected slice is not complete yet", () => {
    const schema = threeDimensionalGridSchema();
    const selected = slicedGridRecords([0]);
    const partial = selected.slice(0, 5).map((item) => ({
      ...item,
      observables: {},
    }));

    render(
      <MeasurementDataPreview
        preview={{ schema, items: selected }}
        slice={{
          ...slicePreview(schema, partial),
          selectedPointCount: 6,
        }}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "The selected slice is incomplete: 5 of 6 points are durable. A complete grid is not available in this saved slice.",
      ),
    ).toBeVisible();
  });

  it.each(
    [0, 4096, 8192].flatMap((offset) =>
      ["full", "sparse", "out-of-order"].map((order) => ({ offset, order })),
    ),
  )(
    "plots only the labelled scalar window at $offset for $order records",
    async ({ offset, order }) => {
      const total = 9000;
      const schema: MeasurementDatasetSchema = {
        ...scalarSchema(),
        dimensions: [{ id: "point", kind: "point", size: total }],
        point_domain: {
          kind: "product_grid",
          axes: [
            gridAxis(
              "bias",
              Array.from({ length: total }, (_, i) => scalar(100 + i / 4, "V")),
            ),
          ],
        },
        variables: [
          ...scalarSchema().variables!,
          {
            id: "other",
            label: "Other",
            role: "observable",
            dtype: "float64",
            unit: "K",
            dims: ["point"],
          },
        ],
      };
      const count = Math.min(4096, total - offset);
      let items = Array.from({ length: count }, (_, i) =>
        record(
          offset + i,
          { bias: scalar(100 + (offset + i) / 4, "V") },
          {
            signal: scalar((offset + i) * 2, "ratio"),
            other:
              i === 2
                ? {
                    kind: "unavailable",
                    reason: "overload",
                    dtype: "float64",
                    unit: "K",
                    shape: [],
                    metadata: {},
                  }
                : scalar(-(offset + i), "K"),
          },
        ),
      );
      if (order === "sparse") items = items.filter((_, i) => i % 3 !== 1);
      if (order === "out-of-order") items.reverse();
      const slice = {
        ...slicePreview(schema, items),
        selectedPointCount: total,
        offset,
        windowPointCount: count,
        truncated: true,
        previousOffset: offset ? offset - 4096 : undefined,
        nextOffset: offset + count < total ? offset + 4096 : undefined,
      };
      const onSliceOffsetChange = vi.fn();
      const props = {
        onSliceOffsetChange,
        preview: { schema, items: [], livePointIndex: 8999 },
        slice,
        sliceError: null,
        slicePending: false,
        fixedAxisIndices: {},
        measurementAxes: { xAxisId: "bias" },
        onFixedAxisIndexChange: vi.fn(),
      };
      const view = render(<MeasurementDataPreview {...props} />);
      expect(onSliceOffsetChange).not.toHaveBeenCalled();
      const lastWindow = screen.getByRole("button", { name: "查看末窗口" });
      expect(lastWindow).toHaveProperty("disabled", offset === 8192);
      fireEvent.click(lastWindow);
      expect(onSliceOffsetChange.mock.calls).toEqual(offset === 8192 ? [] : [[8192]]);
      onSliceOffsetChange.mockClear();
      expect(screen.getByText(/当前窗口，非全量数据/)).toBeVisible();
      const runtime = vi.mocked(EChartRuntime);
      await waitFor(() =>
        expect(runtime.mock.lastCall?.[0].option.series).toEqual([
          expect.objectContaining({
            type: "scatter",
            data: items.map((item) => [100 + item.point_index / 4, item.point_index * 2]),
          }),
        ]),
      );
      expect(screen.getByRole("button", { name: "Previous measurement window" })).toHaveProperty(
        "disabled",
        offset === 0,
      );
      expect(screen.getByRole("button", { name: "Next measurement window" })).toHaveProperty(
        "disabled",
        offset + count === total,
      );
      fireEvent.change(screen.getByLabelText("测量图表"), {
        target: { value: "slice:other:value" },
      });
      await waitFor(() =>
        expect(runtime.mock.lastCall?.[0].option.series).toEqual([
          expect.objectContaining({
            type: "scatter",
            data: items
              .filter((item) => item.observables.other?.kind !== "unavailable")
              .map((item) => [100 + item.point_index / 4, -item.point_index]),
          }),
        ]),
      );
      const before = runtime.mock.lastCall?.[0].interactionKey;
      view.rerender(<MeasurementDataPreview {...props} slicePending={true} />);
      expect(screen.getByRole("button", { name: "查看末窗口" })).toBeDisabled();
      expect(screen.getByText(/正在读取当前切片，图表可能仍显示上次读取的数据/)).toBeVisible();
      expect(runtime.mock.lastCall?.[0].interactionKey).toBe(before);
      view.rerender(
        <MeasurementDataPreview {...props} sliceError={new Error("window read failed")} />,
      );
      expect(
        screen.getByText(/当前切片读取失败，数据可能已过时： window read failed/),
      ).toBeVisible();
      expect(screen.getByRole("button", { name: "查看末窗口" })).toBeDisabled();
      view.rerender(
        <MeasurementDataPreview {...props} slice={{ ...slice, items: items.slice(0, -1) }} />,
      );
      expect(screen.queryByText(/window read failed/)).not.toBeInTheDocument();
      // Receipt order and changing saved records never navigate the reader automatically.
      expect(onSliceOffsetChange).not.toHaveBeenCalled();
      expect(screen.getByLabelText("测量图表")).toHaveValue("slice:other:value");
      view.rerender(<MeasurementDataPreview {...props} slice={{ ...slice, items: [] }} />);
      expect(
        screen.getByText(/当前窗口，非全量数据。此窗口内暂无已保存的可用标量值/),
      ).toBeVisible();
    },
  );

  it("navigates bounded logical-point windows for oversized slices", () => {
    const schema = threeDimensionalGridSchema();
    const items = slicedGridRecords([0]);
    const onSliceOffsetChange = vi.fn();

    render(
      <MeasurementDataPreview
        preview={{ schema, items: [] }}
        slice={{
          ...slicePreview(schema, items),
          selectedPointCount: 10_000,
          offset: 4096,
          windowPointCount: items.length,
          previousOffset: 0,
          nextOffset: 8192,
          truncated: true,
        }}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onSliceOffsetChange={onSliceOffsetChange}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/Points 4,097–4,102 of 10,000/)).toBeVisible();
    expect(screen.queryByText(/当前窗口，非全量数据/)).not.toBeInTheDocument();
    expect(screen.getByText(/The selected slice is too large for an automatic plot/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Previous measurement window" }));
    fireEvent.click(screen.getByRole("button", { name: "Next measurement window" }));
    expect(onSliceOffsetChange.mock.calls).toEqual([[0], [8192]]);
  });

  it("reports selected-slice read failures instead of blaming variable shapes", () => {
    const schema = threeDimensionalGridSchema();

    render(
      <MeasurementDataPreview
        preview={{ schema, items: slicedGridRecords([0]) }}
        sliceError={new Error("offline")}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(screen.getByText("The selected product-grid slice could not be read.")).toBeVisible();
  });

  it("uses authored entity-string and boolean axes as safely labeled fixed coordinates", () => {
    const schema = entitySlicedGridSchema();
    const items = entitySlicedGridRecords().slice(0, 6);

    const heatmaps = planMeasurementCharts(items, schema, { device: 0, enabled: 0 }).filter(
      (chart) => chart.kind === "heatmap",
    );

    expect(heatmaps).toEqual([
      expect.objectContaining({
        id: "heatmap:temperature:row:column:value:fixed:device=%22q1%22%40index%3A0&enabled=true%40index%3A0",
        xLabel: "Row [mm]",
        yLabel: "Column [mm]",
        fixedCoordinates: [
          expect.objectContaining({ id: "device", label: "Device", value: "q1", index: 0 }),
          expect.objectContaining({ id: "enabled", label: "Enabled", value: true, index: 0 }),
        ],
      }),
    ]);

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ device: 0, enabled: 0 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("option", {
        name: 'Temperature heatmap — x: Row [mm] · y: Column [mm] · color: Temperature [K] · fixed: Device="q1", Enabled=true',
      }),
    ).toHaveValue(
      "heatmap:temperature:row:column:value:fixed:device=%22q1%22%40index%3A0&enabled=true%40index%3A0",
    );
  });

  it("keeps a valid unsupported rank-two value in the table", () => {
    const schema: MeasurementDatasetSchema = {
      ...baseSchema(),
      dimensions: [
        { id: "point", kind: "point", size: 1 },
        { id: "row", kind: "sample", size: 2 },
        { id: "column", kind: "sample", size: 2 },
      ],
      variables: [
        {
          id: "image",
          role: "observable",
          dtype: "float64",
          dims: ["point", "row", "column"],
        },
      ],
      primary_observables: ["image"],
    };
    const items = [
      record(
        0,
        {},
        {
          image: {
            kind: "array",
            dtype: "float64",
            shape: [2, 2],
            values: [
              [1, 2],
              [3, 4],
            ],
          },
        },
      ),
    ];

    expect(() => planMeasurementCharts(items, schema)).not.toThrow();
    expect(planMeasurementCharts(items, schema)).toEqual([]);
    expect(measurementTable(items, schema).rows[0]?.cells).toEqual(["0", "2 × 2 samples"]);
  });

  it("does not infer variable contracts while the optional schema is absent", () => {
    const items = [record(0, { bias: scalar(1, "V") }, { signal: scalar(2, "ratio") })];

    expect(planMeasurementCharts(items)).toEqual([]);
    expect(measurementTable(items)).toEqual({
      columns: [{ id: "point", label: "Point", role: "point" }],
      rows: [{ id: "point-0:0", cells: ["0"] }],
    });
  });

  it("plots a sparse one-axis receipt without inventing buffered points or saved data", () => {
    const schema = scalarSchema();
    const latest = record(2, { bias: scalar(0.2, "V") }, { signal: scalar(4, "ratio") });
    const saved = record(0, { bias: scalar(0, "V") }, { signal: scalar(1, "ratio") });
    render(
      <MeasurementDataPreview
        preview={{
          schema,
          items: [saved, latest],
          recordCount: 3,
          durableRecordCount: 1,
          livePointIndex: 2,
        }}
        slice={{ ...slicePreview(schema, [saved]), selectedPointCount: 3 }}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={() => {}}
      />,
    );
    expect(screen.getByRole("region", { name: "测量数据摘要" })).toHaveTextContent("run 已接收 3");
    expect(screen.getByRole("region", { name: "测量数据摘要" })).toHaveTextContent("run 已保存 1");
    expect(screen.getByRole("region", { name: "测量数据摘要" })).toHaveTextContent(
      "当前记录预览 2",
    );
    expect(screen.getByRole("region", { name: "测量数据摘要" })).toHaveTextContent(
      "当前切片已读取 1",
    );
    expect(screen.getByTestId("plot-position")).toHaveTextContent(
      "saved and received preview · 2 available records · highest point index 2",
    );
    expect(screen.getByText(/Partial received data; gaps are not interpolated/)).toBeVisible();
    expect(screen.getByText(/共绘制 2 个点/)).toBeInTheDocument();
    expect(screen.getByText(/最新接收记录（点索引 2）尚未保存/)).toBeVisible();
    expect(screen.queryByText(/共绘制 3 个点/)).not.toBeInTheDocument();
  });

  it("keeps one scan identity when durable data catches up with a live scatter", async () => {
    const schema = entityScalarSchema();
    schema.point_domain = {
      kind: "product_grid",
      axes: [
        gridAxis(
          "bias",
          [0, 1, 2, 3].map((index) => scalar(index / 10, "V")),
        ),
      ],
    };
    schema.dimensions[0]!.size = 4;
    const items = [0, 1, 2, 3].map((index) =>
      record(
        index,
        { bias: scalar(index / 10, "V") },
        { readout: entityArray([index, index + 1], "ratio") },
      ),
    );
    const props = {
      preview: { schema, items: [items[0]!, items[2]!], livePointIndex: 2 },
      slice: slicePreview(schema, [items[0]!]),
      sliceError: null,
      slicePending: false,
      fixedAxisIndices: {},
      onFixedAxisIndexChange: () => {},
    };
    const view = render(<MeasurementDataPreview {...props} />);
    const runtime = vi.mocked(EChartRuntime);
    const lastProps = () => runtime.mock.lastCall?.[0];
    await waitFor(() =>
      expect(lastProps()?.option.series).toEqual(
        expect.arrayContaining([expect.objectContaining({ type: "scatter" })]),
      ),
    );
    const identity = lastProps()!.interactionKey;
    expect(identity).toBeDefined();
    view.rerender(
      <MeasurementDataPreview
        {...props}
        preview={{ schema, items: items.slice(0, 3) }}
        slice={slicePreview(schema, items.slice(0, 3))}
      />,
    );
    await waitFor(() =>
      expect(lastProps()?.option.series).toEqual(
        expect.arrayContaining([expect.objectContaining({ type: "line" })]),
      ),
    );
    expect(lastProps()!.interactionKey).toBe(identity);
    view.rerender(
      <MeasurementDataPreview
        {...props}
        preview={{ schema, items, livePointIndex: 3 }}
        slice={slicePreview(schema, items.slice(0, 3))}
      />,
    );
    await waitFor(() =>
      expect(lastProps()?.option.series).toEqual(
        expect.arrayContaining([expect.objectContaining({ type: "scatter" })]),
      ),
    );
    expect(lastProps()!.interactionKey).toBe(identity);
  });

  it("keeps unknown totals and slice failures visible as the selection changes", () => {
    const schema = scalarSchema();
    const saved = record(0, { bias: scalar(0, "V") }, { signal: scalar(1, "ratio") });
    const props = {
      preview: { schema, items: [saved] },
      slice: slicePreview(schema, [saved]),
      sliceError: null,
      slicePending: false,
      fixedAxisIndices: {},
      onFixedAxisIndexChange: () => {},
    };
    const view = render(<MeasurementDataPreview {...props} />);
    const summary = screen.getByRole("region", { name: "测量数据摘要" });
    expect(summary).toHaveTextContent("run 已接收 未知");
    expect(summary).toHaveTextContent("run 已保存 未知");
    expect(summary).toHaveTextContent("当前切片已读取 1");
    view.rerender(<MeasurementDataPreview {...props} slice={undefined} slicePending={true} />);
    expect(summary).toHaveTextContent("当前切片已读取 未知");
    expect(summary).toHaveTextContent("正在读取当前切片");
    view.rerender(
      <MeasurementDataPreview
        {...props}
        slice={undefined}
        sliceError={new Error("source error 42")}
      />,
    );
    expect(summary).toHaveTextContent("当前切片读取失败，数据可能已过时： source error 42");
    expect(summary).not.toHaveTextContent("当前切片已读取 1");
  });

  // Repeated 4096-row mounts exercise correctness, not a five-second performance budget.
  it("builds 4096 array rows and raw JSON only while open and visible, using current data", async () => {
    const schema: MeasurementDatasetSchema = {
      ...baseSchema(),
      dimensions: [
        { id: "point", kind: "point", size: 4096 },
        { id: "row", kind: "sample", size: 2 },
        { id: "column", kind: "sample", size: 2 },
      ],
      variables: [
        {
          id: "image",
          role: "observable",
          dtype: "float64",
          unit: "V",
          dims: ["point", "row", "column"],
        },
      ],
    };
    const items = Array.from({ length: 4096 }, (_, index) =>
      record(
        index,
        {},
        {
          image: {
            kind: "array",
            dtype: "float64",
            unit: "V",
            shape: [2, 2],
            availability: {
              valid: [
                [true, false],
                [true, true],
              ],
              unavailable: [{ reason: "overload", flat_indices: [1] }],
            },
            values: [
              [index, 2],
              [3, 4],
            ],
          },
        },
      ),
    );
    const next = items.map((item) => ({ ...item, logical_point_id: `new-${item.point_index}` }));
    const table = vi.spyOn(visualization, "measurementTable");
    const stringify = vi.spyOn(JSON, "stringify");
    const showModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "showModal");
    const close = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "close");
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLDialogElement.prototype, "close", {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.dispatchEvent(new Event("close"));
      },
    });
    const viewFor = (records: MeasurementRecord[]) => (
      <MeasurementWorkspace title="Synthetic">
        <MeasurementDataPreview
          preview={{ schema, items: records }}
          sliceError={null}
          slicePending={false}
          fixedAxisIndices={{}}
          onFixedAxisIndexChange={vi.fn()}
        />
      </MeasurementWorkspace>
    );
    try {
      const view = render(viewFor(items));
      expect(table).not.toHaveBeenCalled();
      expect(stringify.mock.calls.some(([value]) => value === items)).toBe(false);
      expect(screen.getByText("Record table · 4096 rows in this view")).toBeVisible();
      fireEvent.click(screen.getByText(/Record table/));
      await screen.findByTestId("measurement-table");
      expect(table).toHaveBeenLastCalledWith(items.slice(0, 100), schema, {}, undefined);
      expect(screen.getByTestId("measurement-table").querySelectorAll("tbody tr")).toHaveLength(
        100,
      );
      const points: string[] = [];
      for (let page = 0; page < 41; page += 1) {
        points.push(
          ...Array.from(
            screen.getByTestId("measurement-table").querySelectorAll("tbody th"),
            (cell) => cell.textContent ?? "",
          ),
        );
        fireEvent.click(screen.getByRole("button", { name: "下一页记录" }));
      }
      expect(points).toEqual(items.map((item) => String(item.point_index)));
      expect(screen.getByRole("button", { name: "下一页记录" })).toBeDisabled();
      expect(table).toHaveBeenLastCalledWith(items.slice(4000), schema, {}, undefined);
      view.rerender(viewFor(next));
      expect(table).toHaveBeenLastCalledWith(next.slice(4000), schema, {}, undefined);
      view.rerender(viewFor(items.slice(0, 205)));
      expect(table).toHaveBeenLastCalledWith(items.slice(200, 205), schema, {}, undefined);
      view.rerender(viewFor(items));
      expect(table).toHaveBeenLastCalledWith(items.slice(200, 300), schema, {}, undefined);
      fireEvent.click(screen.getByRole("button", { name: "末页记录" }));
      expect(table).toHaveBeenLastCalledWith(items.slice(4000), schema, {}, undefined);
      fireEvent.click(screen.getByText("Raw records", { exact: true }));
      expect(await screen.findByTestId("measurement-preview")).toHaveTextContent(
        '"point_index": 4095',
      );
      expect(stringify.mock.calls.some(([value]) => value === items)).toBe(true);
      expect(JSON.parse(screen.getByTestId("measurement-preview").textContent)).toEqual(items);
      fireEvent.click(screen.getByRole("button", { name: "聚焦曲线" }));
      expect(screen.queryByTestId("measurement-table")).not.toBeInTheDocument();
      expect(screen.queryByTestId("measurement-preview")).not.toBeInTheDocument();
      table.mockClear();
      stringify.mockClear();
      view.rerender(viewFor(next));
      expect(table).not.toHaveBeenCalled();
      expect(stringify.mock.calls.some(([value]) => value === next)).toBe(false);
      fireEvent.click(screen.getByRole("button", { name: "退出聚焦，查看 run 详情" }));
      await screen.findByTestId("measurement-table");
      expect(table).toHaveBeenLastCalledWith(next.slice(0, 100), schema, {}, undefined);
      expect(await screen.findByTestId("measurement-preview")).toHaveTextContent(
        '"logical_point_id": "new-4095"',
      );
      fireEvent.click(screen.getByText(/Record table/));
      fireEvent.click(screen.getByText("Raw records", { exact: true }));
      await waitFor(() =>
        expect(screen.queryByTestId("measurement-table")).not.toBeInTheDocument(),
      );
      expect(screen.queryByTestId("measurement-preview")).not.toBeInTheDocument();
      table.mockClear();
      stringify.mockClear();
      view.rerender(viewFor(items));
      expect(table).not.toHaveBeenCalled();
      expect(stringify.mock.calls.some(([value]) => value === items)).toBe(false);
      fireEvent.click(screen.getByText("Raw records", { exact: true }));
      expect(await screen.findByTestId("measurement-preview")).not.toHaveTextContent("new-4095");
    } finally {
      table.mockRestore();
      stringify.mockRestore();
      if (showModal) Object.defineProperty(HTMLDialogElement.prototype, "showModal", showModal);
      else Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
      if (close) Object.defineProperty(HTMLDialogElement.prototype, "close", close);
      else Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
    }
  }, 15_000);

  it("shows the plot first and opens the bounded record table on demand", async () => {
    const schema = scalarSchema();
    const items = [
      record(0, { bias: scalar(0, "V") }, { signal: scalar(1, "ratio") }),
      record(1, { bias: scalar(1, "V") }, { signal: scalar(2, "ratio") }),
    ];

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("img", { name: "Response: Response [ratio] by Bias [V]" }),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Record table/));
    expect(await screen.findByRole("columnheader", { name: /Bias \[V\]/ })).toBeVisible();
    expect(screen.getByText(/Raw records/)).toBeVisible();
    expect(screen.queryByTestId("measurement-preview")).not.toBeInTheDocument();
  });

  it("labels omitted I/Q arrays from confirmed projection without changing the bounded table", async () => {
    const base = scalarSchema();
    const schema: MeasurementDatasetSchema = {
      ...base,
      point_domain: {
        kind: "product_grid",
        axes: [
          gridAxis(
            "bias",
            Array.from({ length: 256 }, (_, i) => ({ ...scalar(i, ""), unit: null })),
          ),
        ],
      },
      dimensions: [
        { id: "point", kind: "point", size: 256 },
        { id: "sample", kind: "sample", size: 128 },
      ],
      variables: [
        { ...base.variables![0]!, unit: null },
        ...["I", "Q"].map((id) => ({
          id,
          role: "observable" as const,
          dtype: "float64" as const,
          dims: ["point", "sample"],
          unit: "V",
        })),
      ],
      primary_observables: ["I", "Q"],
    };
    const items = Array.from({ length: 256 }, (_, point) =>
      record(
        point,
        { bias: scalar(point, "V") },
        {
          I: array(
            Array.from({ length: 128 }, (_sample, i) => point + i),
            "V",
          ),
          Q: array(
            Array.from({ length: 128 }, (_sample, i) => point - i),
            "V",
          ),
        },
      ),
    );
    const projected = items.map((item) => ({ ...item, observables: {} }));
    expect(measurementSlicePlan(schema)?.variableIds).toEqual(["bias"]);
    // Legacy/unknown projection must not be guessed from a missing array value.
    expect(measurementTable(projected.slice(0, 1), schema).rows[0]?.cells.slice(-2)).toEqual([
      "—",
      "—",
    ]);
    expect(measurementTable(items.slice(0, 1), schema).rows[0]?.cells.slice(-2)).toEqual([
      "128 samples V",
      "128 samples V",
    ]);
    render(
      <MeasurementDataPreview
        preview={{ schema, items: items.slice(0, 1) }}
        slice={{ ...slicePreview(schema, projected), variableIds: ["bias"] }}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(/Record table/));
    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("未在当前切片读取")).toHaveLength(200);
    expect(within(table).queryByText("—")).not.toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(101);
    fireEvent.click(screen.getByRole("button", { name: "末页记录" }));
    expect(within(table).getAllByText("未在当前切片读取")).toHaveLength(112);
    expect(within(table).getAllByRole("row")).toHaveLength(57);
    expect(within(table).getByRole("rowheader", { name: "255" })).toBeVisible();
  });

  it("does not relabel requested missing or unavailable values as unrequested", () => {
    const schema = scalarSchema();
    const items = [
      record(0, { bias: scalar(0, "V") }, {}),
      record(
        1,
        { bias: scalar(1, "V") },
        {
          signal: {
            kind: "unavailable",
            reason: "overload",
            dtype: "float64",
            metadata: {},
            shape: [],
            unit: null,
          },
        },
      ),
    ];
    expect(
      measurementTable(items, schema, {}, ["bias", "signal"]).rows.map((r) => r.cells.at(-1)),
    ).toEqual(["—", "Unavailable · overload"]);
    expect(measurementTable(items, schema, {}, []).rows[0]?.cells).toEqual([
      "0",
      "未在当前切片读取",
      "未在当前切片读取",
    ]);
  });

  it("keeps a live daemon receipt visible while the selected slice is not durable", async () => {
    const schema = threeDimensionalGridSchema();
    const liveRecord = slicedGridRecords([0])[0]!;

    render(
      <MeasurementDataPreview
        preview={{ schema, items: [liveRecord], livePointIndex: 0 }}
        slice={slicePreview(schema, [])}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{ bias: 0 }}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Raw records", { exact: true }));
    expect(await screen.findByTestId("measurement-preview")).toHaveTextContent('"point_index": 0');
  });

  it("offers every chart candidate instead of silently truncating observables", () => {
    const variables = Array.from({ length: 8 }, (_item, index) => ({
      id: `signal-${index}`,
      label: `Response ${index + 1}`,
      role: "observable" as const,
      dtype: "float64" as const,
      unit: "ratio",
      dims: ["point"],
    }));
    const schema: MeasurementDatasetSchema = {
      ...scalarSchema(),
      variables: [scalarSchema().variables![0]!, ...variables],
      primary_observables: variables.map((variable) => variable.id),
    };
    const items = [0, 1].map((point) =>
      record(
        point,
        { bias: scalar(point, "V") },
        Object.fromEntries(
          variables.map((variable, index) => [variable.id, scalar(point + index, "ratio")]),
        ),
      ),
    );

    render(
      <MeasurementDataPreview
        preview={{ schema, items }}
        slice={slicePreview(schema, items)}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "测量图表" });
    expect(selector).toHaveValue("scalar:signal-0:bias:value");
    expect(screen.getAllByRole("option")).toHaveLength(8);
    expect(screen.getByText("8 个候选图表")).toBeVisible();

    fireEvent.change(selector, { target: { value: "scalar:signal-7:bias:value" } });
    expect(
      screen.getByRole("img", { name: "Response 8: Response 8 [ratio] by Bias [V]" }),
    ).toBeVisible();
  });
});

function baseSchema(): MeasurementDatasetSchema {
  return {
    format_version: "scopecat.measurement_dataset_schema.v17",
    dataset_id: "raw-measurements",
    record_schema: "scopecat.measurement_record.v10",
    point_domain: { kind: "point_cloud", columns: [] },
    dimensions: [{ id: "point", kind: "point", size: 3 }],
    variables: [],
  };
}

function scalarSchema(): MeasurementDatasetSchema {
  return {
    ...baseSchema(),
    point_domain: {
      kind: "product_grid",
      axes: [gridAxis("bias", [scalar(0, "V"), scalar(0.1, "V"), scalar(0.2, "V")])],
    },
    variables: [
      {
        id: "bias",
        label: "Bias",
        role: "coordinate",
        dtype: "float64",
        unit: "V",
        dims: ["point"],
      },
      {
        id: "signal",
        label: "Response",
        role: "observable",
        dtype: "float64",
        unit: "ratio",
        dims: ["point"],
      },
    ],
    primary_coordinates: ["bias"],
    primary_observables: ["signal"],
  };
}

function twoDimensionalPointCloudSchema(): MeasurementDatasetSchema {
  return {
    ...baseSchema(),
    point_domain: {
      kind: "point_cloud",
      columns: [{ id: "x" }, { id: "y" }],
    },
    variables: [
      {
        id: "x",
        label: "X",
        role: "coordinate",
        dtype: "float64",
        unit: "mm",
        dims: ["point"],
      },
      {
        id: "y",
        label: "Y",
        role: "coordinate",
        dtype: "float64",
        unit: "mm",
        dims: ["point"],
      },
      {
        id: "temperature",
        label: "Temperature",
        role: "observable",
        dtype: "float64",
        unit: "K",
        dims: ["point"],
      },
    ],
    primary_coordinates: ["y", "x"],
    primary_observables: ["temperature"],
  };
}

function twoDimensionalGridSchema(
  dtype: "complex128" | "float64" = "float64",
): MeasurementDatasetSchema {
  return {
    ...baseSchema(),
    dimensions: [{ id: "point", kind: "point", size: 6 }],
    point_domain: {
      kind: "product_grid",
      axes: [
        gridAxis("row", [scalar(10, "mm"), scalar(20, "mm")]),
        gridAxis("column", [scalar(1, "mm"), scalar(2, "mm"), scalar(3, "mm")]),
      ],
    },
    variables: [
      {
        id: "column",
        label: "Column",
        role: "coordinate",
        dtype: "float64",
        unit: "mm",
        dims: ["point"],
      },
      {
        id: "row",
        label: "Row",
        role: "coordinate",
        dtype: "float64",
        unit: "mm",
        dims: ["point"],
      },
      {
        id: "temperature",
        label: "Temperature",
        role: "observable",
        dtype,
        unit: dtype === "complex128" ? "ratio" : "K",
        dims: ["point"],
      },
    ],
    primary_coordinates: ["column", "row"],
    primary_observables: ["temperature"],
  };
}

function threeDimensionalGridSchema(): MeasurementDatasetSchema {
  const schema = twoDimensionalGridSchema();
  return {
    ...schema,
    dimensions: [{ id: "point", kind: "point", size: 12 }],
    point_domain: {
      kind: "product_grid",
      axes: [
        gridAxis("row", [scalar(10, "mm"), scalar(20, "mm")]),
        gridAxis("column", [scalar(1, "mm"), scalar(2, "mm"), scalar(3, "mm")]),
        gridAxis("bias", [scalar(0, "V"), scalar(1, "V")]),
      ],
    },
    variables: [
      {
        id: "bias",
        label: "Bias",
        role: "coordinate",
        dtype: "float64",
        unit: "V",
        dims: ["point"],
      },
      ...schema.variables!,
    ],
    primary_coordinates: ["bias", "column", "row"],
  };
}

function opaqueSlicedGridSchema(): MeasurementDatasetSchema {
  const schema = twoDimensionalGridSchema();
  return {
    ...schema,
    dimensions: [{ id: "point", kind: "point", size: 12 }],
    point_domain: {
      kind: "product_grid",
      axes: [
        ...(schema.point_domain.kind === "product_grid" ? schema.point_domain.axes : []),
        gridAxis("opaque", [null, null]),
      ],
    },
  };
}

function duplicateFixedGridSchema(): MeasurementDatasetSchema {
  const schema = threeDimensionalGridSchema();
  return {
    ...schema,
    point_domain: {
      kind: "product_grid",
      axes:
        schema.point_domain.kind === "product_grid"
          ? schema.point_domain.axes.map((axis) =>
              axis.id === "bias" ? gridAxis("bias", [scalar(0, "V"), scalar(0, "V")]) : axis,
            )
          : [],
    },
  };
}

function largeFixedGridSchema(): MeasurementDatasetSchema {
  const schema = threeDimensionalGridSchema();
  return {
    ...schema,
    dimensions: [{ id: "point", kind: "point", size: 1_800 }],
    point_domain: {
      kind: "product_grid",
      axes:
        schema.point_domain.kind === "product_grid"
          ? schema.point_domain.axes.map((axis) =>
              axis.id === "bias"
                ? gridAxis(
                    "bias",
                    Array.from({ length: 300 }, (_value, index) => scalar(index, "V")),
                  )
                : axis,
            )
          : [],
    },
  };
}

function mixedGridSchema(): MeasurementDatasetSchema {
  const schema = threeDimensionalGridSchema();
  return {
    ...schema,
    dimensions: [...schema.dimensions, { id: "sample", kind: "sample", size: 3 }],
    variables: [
      ...schema.variables!,
      {
        id: "frequency",
        label: "Frequency",
        role: "coordinate",
        dtype: "float64",
        unit: "GHz",
        dims: ["point", "sample"],
        recording_group_id: "spectrum",
      },
      {
        id: "spectrum",
        label: "Spectrum",
        role: "observable",
        dtype: "float64",
        unit: "ratio",
        dims: ["point", "sample"],
        recording_group_id: "spectrum",
      },
    ],
    primary_coordinates: [...schema.primary_coordinates!, "frequency"],
    primary_observables: [...schema.primary_observables!, "spectrum"],
  };
}

function traceOnlyRaggedGridSchema(): MeasurementDatasetSchema {
  const schema = mixedGridSchema();
  return {
    ...schema,
    dimensions: schema.dimensions.map((dimension) =>
      dimension.id === "sample" ? { ...dimension, size: null } : dimension,
    ),
    variables: schema.variables?.filter((variable) => variable.id !== "temperature"),
    primary_observables: ["spectrum"],
  };
}

function entityScalarSchema(): MeasurementDatasetSchema {
  return {
    ...baseSchema(),
    point_domain: {
      kind: "product_grid",
      axes: [gridAxis("bias", [scalar(0, "V"), scalar(0.1, "V"), scalar(0.2, "V")])],
    },
    dimensions: [{ id: "point", kind: "point", size: 3 }, entityDimension()],
    variables: [
      {
        id: "bias",
        label: "Bias",
        role: "coordinate",
        dtype: "float64",
        unit: "V",
        dims: ["point"],
      },
      entityVariable({
        id: "readout",
        label: "Readout",
        role: "observable",
        dtype: "float64",
        unit: "ratio",
        dims: ["point", "qubit"],
      }),
    ],
    primary_coordinates: ["bias"],
    primary_observables: ["readout"],
  };
}

function entityScalarSchemaWithEntityCount(size: number): MeasurementDatasetSchema {
  const schema = entityScalarSchema();
  const values = Array.from({ length: size }, (_value, index) => ({
    id: `q${index}`,
    kind: "qubit",
    metadata: { label: `Q${index}` },
  }));
  return {
    ...schema,
    dimensions: schema.dimensions.map((dimension) =>
      dimension.id === "qubit"
        ? { ...dimension, size, index: { kind: "entity", values } }
        : dimension,
    ),
    variables: schema.variables?.map((variable) =>
      variable.source_entity_products
        ? {
            ...variable,
            source_entity_products: {
              ...variable.source_entity_products,
              product_ids: values.map((entity) => `readout/${entity.id}`),
            },
          }
        : variable,
    ),
  };
}

function entityTraceSchema(ragged: boolean): MeasurementDatasetSchema {
  const frequency = entityVariable({
    id: "frequency",
    label: "Frequency",
    role: "coordinate",
    dtype: "float64",
    unit: "GHz",
    dims: ["point", "qubit", "sample"],
    recording_group_id: "readout",
  });
  const trace = entityVariable({
    id: "trace",
    label: "Trace",
    role: "observable",
    dtype: "float64",
    unit: ragged ? "V" : "ratio",
    dims: ["point", "qubit", "sample"],
    recording_group_id: "readout",
  });
  return {
    ...baseSchema(),
    dimensions: [
      { id: "point", kind: "point", size: 1 },
      entityDimension(),
      { id: "sample", kind: "record_axis", size: ragged ? null : 3 },
    ],
    variables: ragged ? [trace] : [frequency, trace],
    variable_groups: [{ id: "readout" }],
    primary_coordinates: ragged ? [] : ["frequency"],
    primary_observables: ["trace"],
  };
}

function entityDimension(): MeasurementDatasetSchema["dimensions"][number] {
  return {
    id: "qubit",
    kind: "entity",
    label: "Qubit",
    size: 2,
    index: {
      kind: "entity",
      values: [
        { id: "q0", kind: "qubit", metadata: { label: "Q0" } },
        { id: "q1", kind: "qubit", metadata: { label: "Q1" } },
      ],
    },
  };
}

function entityVariable(
  variable: NonNullable<MeasurementDatasetSchema["variables"]>[number],
): NonNullable<MeasurementDatasetSchema["variables"]>[number] {
  return {
    ...variable,
    entity_acquisition: { policy: "best_effort", cohort_id: "readout-batch" },
    source_entity_products: {
      dimension_id: "qubit",
      product_ids: ["readout/q0", "readout/q1"],
    },
  };
}

function opaqueOnlyGridSchema(): MeasurementDatasetSchema {
  return {
    ...baseSchema(),
    dimensions: [{ id: "point", kind: "point", size: 4 }],
    point_domain: {
      kind: "product_grid",
      axes: [
        gridAxis("opaque", [null, null]),
        gridAxis("device", [stringScalar("q1"), stringScalar("q2")]),
      ],
    },
    variables: [
      {
        id: "device",
        label: "Device",
        role: "coordinate",
        dtype: "string",
        dims: ["point"],
      },
    ],
    primary_coordinates: ["device"],
  };
}

function entitySlicedGridSchema(): MeasurementDatasetSchema {
  const schema = twoDimensionalGridSchema();
  return {
    ...schema,
    dimensions: [{ id: "point", kind: "point", size: 12 }],
    point_domain: {
      kind: "product_grid",
      axes: [
        gridAxis("device", [stringScalar("q1"), stringScalar("q2")]),
        gridAxis("enabled", [boolScalar(true)]),
        gridAxis("row", [scalar(10, "mm"), scalar(20, "mm")]),
        gridAxis("column", [scalar(1, "mm"), scalar(2, "mm"), scalar(3, "mm")]),
      ],
    },
    variables: [
      {
        id: "device",
        label: "Device",
        role: "coordinate",
        dtype: "string",
        dims: ["point"],
        metadata: { entity_kind: "qubit" },
      },
      {
        id: "enabled",
        label: "Enabled",
        role: "coordinate",
        dtype: "bool",
        dims: ["point"],
      },
      ...schema.variables!,
    ],
  };
}

function entitySlicedGridRecords(): MeasurementRecord[] {
  return ["q1", "q2"].flatMap((device, deviceIndex) =>
    [10, 20].flatMap((row) =>
      [1, 2, 3].map((column, columnIndex) => {
        const point = deviceIndex * 6 + (row === 10 ? 0 : 3) + columnIndex;
        return record(
          point,
          {
            column: scalar(column, "mm"),
            device: stringScalar(device),
            enabled: boolScalar(true),
            row: scalar(row, "mm"),
          },
          { temperature: scalar(point + 10, "K") },
        );
      }),
    ),
  );
}

function slicedGridRecords(biasValues: number[] = [0, 1]): MeasurementRecord[] {
  return biasValues.flatMap((bias, biasIndex) =>
    [10, 20].flatMap((row, rowIndex) =>
      [1, 2, 3].map((column, columnIndex) => {
        const cellIndex = rowIndex * 3 + columnIndex;
        const authoredBiasIndex = bias === 0 ? 0 : bias === 1 ? 1 : biasIndex;
        const point = cellIndex * 2 + authoredBiasIndex;
        return record(
          point,
          {
            bias: scalar(bias, "V"),
            column: scalar(column, "mm"),
            row: scalar(row, "mm"),
          },
          { temperature: scalar(bias * 100 + cellIndex + 10, "K") },
        );
      }),
    ),
  );
}

function mixedGridRecords(biasValues: number[] = [0, 1]): MeasurementRecord[] {
  return slicedGridRecords(biasValues).map((item) => ({
    ...item,
    coordinates: {
      ...item.coordinates,
      frequency: array([4, 5, 6], "GHz"),
    },
    observables: {
      ...item.observables,
      spectrum: array([item.point_index, item.point_index + 1, item.point_index + 2], "ratio"),
    },
  }));
}

function traceOnlyRaggedGridRecords(biasValues: number[] = [0, 1]): MeasurementRecord[] {
  return mixedGridRecords(biasValues).map((item) => ({
    ...item,
    observables: { spectrum: item.observables.spectrum! },
  }));
}

function gridRecords(observable: (point: number) => MeasurementValue): MeasurementRecord[] {
  return [10, 20].flatMap((row) =>
    [1, 2, 3].map((column, point) =>
      record(
        (row === 10 ? 0 : 3) + point,
        { column: scalar(column, "mm"), row: scalar(row, "mm") },
        { temperature: observable((row === 10 ? 0 : 3) + point) },
      ),
    ),
  );
}

function record(
  pointIndex: number,
  coordinates: Record<string, MeasurementValue>,
  observables: Record<string, MeasurementValue>,
): MeasurementRecord {
  return {
    run_id: "run-1",
    logical_point_id: `point-${pointIndex}`,
    point_index: pointIndex,
    coordinates,
    observables,
  };
}

function scalar(value: number, unit: string): Extract<MeasurementValue, { kind: "scalar" }> {
  return { kind: "scalar", dtype: "float64", unit, value };
}

function gridAxis(id: string, values: Array<Extract<MeasurementValue, { kind: "scalar" }> | null>) {
  return { id, size: values.length, source: { kind: "values" as const, values } };
}

function slicePreview(schema: MeasurementDatasetSchema, items: MeasurementRecord[]) {
  return {
    items,
    schema,
    selectedPointCount: items.length,
    offset: 0,
    windowPointCount: items.length,
    truncated: false,
  };
}

function complexScalar(
  real: number,
  imag: number,
  unit: string,
): Extract<MeasurementValue, { kind: "scalar" }> {
  return { kind: "scalar", dtype: "complex128", unit, value: { real, imag } };
}

function stringScalar(value: string): Extract<MeasurementValue, { kind: "scalar" }> {
  return { kind: "scalar", dtype: "string", value };
}

function boolScalar(value: boolean): Extract<MeasurementValue, { kind: "scalar" }> {
  return { kind: "scalar", dtype: "bool", value };
}

function array(values: number[], unit: string): Extract<MeasurementValue, { kind: "array" }> {
  return { kind: "array", dtype: "float64", unit, shape: [values.length], values };
}

function entityArray(values: number[], unit: string): Extract<MeasurementValue, { kind: "array" }> {
  return { kind: "array", dtype: "float64", unit, shape: [values.length], values };
}

function entityMatrix(
  values: number[][],
  unit: string,
  valid?: boolean[][],
): Extract<MeasurementValue, { kind: "array" }> {
  const flatValid = valid?.flat();
  const invalid = flatValid?.flatMap((available, index) => (available ? [] : [index])) ?? [];
  return {
    kind: "array",
    dtype: "float64",
    unit,
    shape: [values.length, values[0]?.length ?? 0],
    values,
    availability:
      valid && invalid.length > 0
        ? {
            valid,
            unavailable: [{ reason: "missing", flat_indices: invalid }],
          }
        : undefined,
  };
}

it.each([
  [1, 0],
  [4096, 0],
  [4097, 4096],
  [8192, 4096],
  [9000, 8192],
  [0, undefined],
  [-1, undefined],
  [Infinity, undefined],
  [1.5, undefined],
  [Number.MAX_SAFE_INTEGER + 1, undefined],
])("locates the logical last window for %s points", (count, offset) => {
  expect(lastMeasurementWindowOffset(count)).toBe(offset);
});
