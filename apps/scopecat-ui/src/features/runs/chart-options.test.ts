import { describe, expect, it } from "vitest";
import type { CustomSeriesRenderItem, EChartsCoreOption } from "echarts";
import type { MeasurementChartPlan } from "./measurement-visualization";
import { analysisFigureOption, measurementChartOption } from "./chart-options";

describe("run chart ECharts options", () => {
  it("builds value axes, inside zoom, and a scroll legend for measurement lines", () => {
    const chart: MeasurementChartPlan = {
      id: "line",
      kind: "line",
      title: "Responses",
      xLabel: "Bias [V]",
      yLabel: "Response [ratio]",
      series: [
        {
          id: "first",
          label: "First",
          points: [
            { x: 1, y: 2 },
            { x: 2, y: 3 },
          ],
        },
        {
          id: "second",
          label: "Second",
          points: [
            { x: 1, y: 4 },
            { x: 2, y: 5 },
          ],
        },
      ],
    };

    const option = measurementChartOption(chart);
    const series = optionSeries(option);

    expect(option.xAxis).toMatchObject({ name: "Bias [V]", scale: true, type: "value" });
    expect(option.yAxis).toMatchObject({ name: "Response [ratio]", scale: true, type: "value" });
    expect(option.dataZoom).toEqual([
      expect.objectContaining({ type: "inside", xAxisIndex: 0, zoomOnMouseWheel: "shift" }),
      expect.objectContaining({ type: "inside", yAxisIndex: 0, zoomOnMouseWheel: "shift" }),
    ]);
    expect(option.legend).toMatchObject({ data: ["First", "Second"], type: "scroll" });
    expect(option.visualMap).toBeUndefined();
    expect(series).toMatchObject([
      {
        data: [
          [1, 2],
          [2, 3],
        ],
        name: "First",
        type: "line",
      },
      {
        data: [
          [1, 4],
          [2, 5],
        ],
        name: "Second",
        type: "line",
      },
    ]);
  });

  it("keeps full series names while compacting oversized legend labels", () => {
    const longLabel = `Delay ${"1234567890".repeat(5)} · Q0`;
    const chart: MeasurementChartPlan = {
      id: "long-legend",
      kind: "line",
      title: "Responses",
      xLabel: "Shot",
      yLabel: "Response [ratio]",
      series: [
        { id: "first", label: longLabel, points: [{ x: 0, y: 1 }] },
        { id: "second", label: "Delay 128 ns · Q1", points: [{ x: 0, y: 2 }] },
      ],
    };

    const option = measurementChartOption(chart);
    const legend = option.legend as {
      formatter: (label: string) => string;
      tooltip: { show: boolean };
    };

    expect(legend.formatter(longLabel)).toBe(`${longLabel.slice(0, 32)}…${longLabel.slice(-15)}`);
    expect(legend.tooltip).toEqual({ show: true });
    expect(optionSeries(option)[0]?.name).toBe(longLabel);
  });

  it("maps point color through visualMap dimension two for color scatter", () => {
    const chart: MeasurementChartPlan = {
      colorLabel: "Temperature [K]",
      id: "color-scatter",
      kind: "color-scatter",
      series: [
        {
          id: "temperature",
          label: "Temperature",
          points: [
            { color: 10, x: 0, y: 2 },
            { color: 30, x: 1, y: 3 },
          ],
        },
      ],
      title: "Temperature map",
      xLabel: "X [mm]",
      yLabel: "Y [mm]",
    };

    const option = measurementChartOption(chart);

    expect(optionSeries(option)[0]).toMatchObject({
      data: [
        [0, 2, 10],
        [1, 3, 30],
      ],
      type: "scatter",
    });
    expect(option.visualMap).toMatchObject({
      dimension: 2,
      max: 30,
      min: 10,
      text: ["Temperature [K]", ""],
    });
  });

  it("uses true midpoint cell boundaries for a non-uniform heatmap axis", () => {
    const chart: MeasurementChartPlan = {
      colorLabel: "Signal [ratio]",
      fixedCoordinates: [],
      grid: { xValues: [0, 1, 10], yValues: [0, 2] },
      id: "non-uniform-heatmap",
      kind: "heatmap",
      series: [
        {
          id: "signal",
          label: "Signal",
          points: [
            { color: 1, x: 0, y: 0 },
            { color: 2, x: 0, y: 2 },
            { color: 3, x: 1, y: 0 },
            { color: 4, x: 1, y: 2 },
            { color: 5, x: 10, y: 0 },
            { color: 6, x: 10, y: 2 },
          ],
        },
      ],
      title: "Signal heatmap",
      xLabel: "Frequency [GHz]",
      yLabel: "Power [dBm]",
    };

    const option = measurementChartOption(chart);
    const [series] = optionSeries(option) as Array<{
      data: number[][];
      encode: Record<string, number[]>;
      renderItem: CustomSeriesRenderItem;
      type: string;
    }>;

    expect(option.xAxis).toMatchObject({
      max: 14.5,
      min: -0.5,
      name: "Frequency [GHz]",
      type: "value",
    });
    expect(option.yAxis).toMatchObject({ max: 3, min: -1, type: "value" });
    expect(option.dataZoom).toBeUndefined();
    expect(option.visualMap).toMatchObject({ dimension: 4, max: 6, min: 1 });
    expect(series).toMatchObject({
      encode: { tooltip: [5, 6, 4], x: [0, 1], y: [2, 3] },
      type: "custom",
    });
    expect(series?.data).toEqual([
      [-0.5, 0.5, -1, 1, 1, 0, 0],
      [-0.5, 0.5, 1, 3, 2, 0, 2],
      [0.5, 5.5, -1, 1, 3, 1, 0],
      [0.5, 5.5, 1, 3, 4, 1, 2],
      [5.5, 14.5, -1, 1, 5, 10, 0],
      [5.5, 14.5, 1, 3, 6, 10, 2],
    ]);
    expect([series?.data[0], series?.data[2], series?.data[4]].map(cellWidth)).toEqual([1, 5, 9]);

    const rendered = series?.renderItem(
      {} as Parameters<CustomSeriesRenderItem>[0],
      {
        coord: (values: unknown) => (values as number[]).map((value) => value * 10),
        value: (dimension: string | number) => series.data[2]![Number(dimension)]!,
        visual: () => "#123456",
      } as unknown as Parameters<CustomSeriesRenderItem>[1],
    );
    expect(rendered).toMatchObject({
      shape: { height: 20, width: 50, x: 5, y: -10 },
      style: { fill: "#123456" },
      type: "rect",
    });
  });

  it("builds analysis series, axes, tooltip, zoom, and legend from authored content", () => {
    const option = analysisFigureOption({
      total_points: 5,
      truncated: false,
      layers: [
        {
          id: "data",
          source: { kind: "dataset", output_id: "data" },
          projection: { kind: "line", x: "bias", y: "frequency" },
          total_points: 5,
          truncated: false,
          preview: {
            kind: "line",
            series: [
              { id: "fit", label: "Fit", x: [-1, 0, 1], y: [2, 3, 2] },
              { id: "reference", label: "Reference", x: [-1, 1], y: [2.5, 2.5] },
            ],
            x_axis: { label: "Bias", unit: "V" },
            y_axis: { label: "Frequency", unit: "GHz" },
          },
        },
      ],
    });

    expect(option.xAxis).toMatchObject({ name: "Bias (V)", scale: true, type: "value" });
    expect(option.yAxis).toMatchObject({ name: "Frequency (GHz)", scale: true, type: "value" });
    expect(option.legend).toMatchObject({ data: ["Fit", "Reference"], type: "scroll" });
    expect(option.tooltip).toMatchObject({ trigger: "axis" });
    expect(option.dataZoom).toEqual([
      expect.objectContaining({ type: "inside", xAxisIndex: 0 }),
      expect.objectContaining({ type: "inside", yAxisIndex: 0 }),
    ]);
    expect(optionSeries(option)).toMatchObject([
      {
        data: [
          [-1, 2],
          [0, 3],
          [1, 2],
        ],
        name: "Fit",
        type: "line",
      },
      {
        data: [
          [-1, 2.5],
          [1, 2.5],
        ],
        name: "Reference",
        type: "line",
      },
    ]);
  });
  it.each(["band", "bars"] as const)("renders mixed layers with declared %s bounds", (style) => {
    const option = analysisFigureOption({
      total_points: 4,
      truncated: false,
      layers: [
        {
          id: "measured",
          source: { kind: "dataset", output_id: "observations" },
          projection: { kind: "scatter", x: "x", y: "y" },
          total_points: 2,
          truncated: false,
          preview: {
            kind: "scatter",
            x_axis: { label: "Time", unit: "us" },
            y_axis: { label: "Signal", unit: "V" },
            series: [{ id: "data", x: [0, 1], y: [1, 2] }],
          },
        },
        {
          id: "fit",
          source: { kind: "dataset", output_id: "curve" },
          projection: {
            kind: "line",
            x: "x",
            y: "y",
            uncertainty: { lower: "lo", upper: "hi", meaning: "One residual scale", style },
          },
          total_points: 2,
          truncated: false,
          preview: {
            kind: "line",
            x_axis: { label: "Time", unit: "us" },
            y_axis: { label: "Signal", unit: "V" },
            series: [
              { id: "data", x: [0, 1], y: [1, 2], y_lower: [0.9, 1.9], y_upper: [1.1, 2.1] },
            ],
          },
        },
      ],
    });
    const series = optionSeries(option);
    expect(series).toMatchObject([
      { id: "measured/data", type: "scatter" },
      { id: "fit/data", type: "line" },
      { id: "fit/data/uncertainty", type: "custom", silent: true },
    ]);
    expect(series[2]?.data).toEqual(
      style === "band"
        ? [[0, 0.9, 1.1, 1, 1.9, 2.1]]
        : [
            [0, 0.9, 1.1],
            [1, 1.9, 2.1],
          ],
    );
    const renderItem = series[2]?.renderItem as CustomSeriesRenderItem;
    const values = [0, 0.9, 1.1, 1, 1.9, 2.1];
    const rendered = renderItem(
      {} as Parameters<CustomSeriesRenderItem>[0],
      {
        value: (index: number) => values[index],
        coord: (value: number[]) => value,
        visual: () => "#123456",
      } as unknown as Parameters<CustomSeriesRenderItem>[1],
    );
    expect(rendered).toMatchObject({ type: style === "band" ? "polygon" : "group" });
  });
});

function optionSeries(option: EChartsCoreOption): Array<Record<string, unknown>> {
  return option.series as Array<Record<string, unknown>>;
}

function cellWidth(cell: number[] | undefined): number | undefined {
  return cell === undefined ? undefined : cell[1]! - cell[0]!;
}
