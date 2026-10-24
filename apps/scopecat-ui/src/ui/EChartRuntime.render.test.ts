// @vitest-environment node
import { expect, it, vi } from "vitest";
import { init } from "echarts/core";
import "./EChartRuntime";

it("renders measurement series and updates their visible range with the registered modules", () => {
  const errors = vi.spyOn(console, "error");
  const chart = init(null, undefined, { renderer: "svg", ssr: true, width: 640, height: 240 });
  try {
    chart.setOption({
      animation: false,
      legend: { type: "scroll" },
      tooltip: { trigger: "item" },
      xAxis: { type: "value", min: 0, max: 10 },
      yAxis: { type: "value" },
      dataZoom: [{ type: "inside", start: 0, end: 100 }],
      visualMap: {
        min: 0,
        max: 10,
        dimension: 1,
        seriesIndex: 1,
        inRange: { color: ["#112233", "#445566"] },
      },
      series: [
        {
          name: "Trace",
          type: "line",
          data: [
            [1, 2],
            [9, 7],
          ],
          lineStyle: { color: "#abcdef" },
        },
        {
          name: "Samples",
          type: "scatter",
          data: [
            [2, 0],
            [8, 10],
          ],
        },
        {
          name: "Interval",
          type: "custom",
          data: [1],
          renderItem: () => ({
            type: "rect",
            shape: { x: 120, y: 80, width: 20, height: 10 },
            style: { fill: "#fedcba" },
          }),
        },
      ],
    });
    const svg = chart.renderToSVGString();
    for (const content of [
      "Trace",
      "Samples",
      "Interval",
      "#abcdef",
      "#fedcba",
      'fill="rgb(17,34,51)"',
      'fill="rgb(68,85,102)"',
    ]) {
      expect(svg).toContain(content);
    }
    chart.dispatchAction({ type: "dataZoom", start: 25, end: 75 });
    expect(chart.getOption().dataZoom).toEqual([expect.objectContaining({ start: 25, end: 75 })]);
    expect(chart.renderToSVGString()).not.toBe(svg);
    expect(errors).not.toHaveBeenCalled();
  } finally {
    chart.dispose();
    errors.mockRestore();
  }
});
