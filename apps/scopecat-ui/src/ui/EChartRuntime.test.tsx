// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EChartsCoreOption } from "echarts/core";

const runtime = vi.hoisted(() => {
  const chart = {
    dispose: vi.fn(),
    resize: vi.fn(),
    setOption: vi.fn(),
  };
  return {
    chart,
    components: {
      customChart: { id: "custom-chart" },
      dataZoomInside: { id: "data-zoom-inside" },
      grid: { id: "grid" },
      legendScroll: { id: "legend-scroll" },
      lineChart: { id: "line-chart" },
      scatterChart: { id: "scatter-chart" },
      svgRenderer: { id: "svg-renderer" },
      tooltip: { id: "tooltip" },
      visualMapContinuous: { id: "visual-map-continuous" },
    },
    init: vi.fn(() => chart),
    use: vi.fn(),
  };
});

vi.mock("echarts/charts", () => ({
  CustomChart: runtime.components.customChart,
  LineChart: runtime.components.lineChart,
  ScatterChart: runtime.components.scatterChart,
}));
vi.mock("echarts/components", () => ({
  DataZoomInsideComponent: runtime.components.dataZoomInside,
  GridComponent: runtime.components.grid,
  LegendScrollComponent: runtime.components.legendScroll,
  TooltipComponent: runtime.components.tooltip,
  VisualMapContinuousComponent: runtime.components.visualMapContinuous,
}));
vi.mock("echarts/core", () => ({ init: runtime.init, use: runtime.use }));
vi.mock("echarts/renderers", () => ({ SVGRenderer: runtime.components.svgRenderer }));

import { EChartRuntime } from "./EChartRuntime";

beforeEach(() => {
  runtime.init.mockReturnValue(runtime.chart);
  vi.stubGlobal("ResizeObserver", undefined);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EChartRuntime", () => {
  it("registers the custom chart and the exact modular ECharts features", async () => {
    vi.resetModules();
    await import("./EChartRuntime");

    expect(runtime.use).toHaveBeenCalledTimes(1);
    expect(runtime.use).toHaveBeenCalledWith([
      runtime.components.customChart,
      runtime.components.dataZoomInside,
      runtime.components.grid,
      runtime.components.legendScroll,
      runtime.components.lineChart,
      runtime.components.scatterChart,
      runtime.components.svgRenderer,
      runtime.components.tooltip,
      runtime.components.visualMapContinuous,
    ]);
  });

  it("initializes, updates, resizes, and disposes one SVG chart instance", () => {
    const firstOption: EChartsCoreOption = { series: [] };
    const secondOption: EChartsCoreOption = { series: [{ data: [[1, 2]], type: "line" }] };
    const view = render(<EChartRuntime height={240} option={firstOption} />);

    expect(runtime.init).toHaveBeenCalledWith(expect.any(HTMLDivElement), undefined, {
      height: 240,
      renderer: "svg",
      width: 640,
    });
    expect(runtime.chart.setOption).toHaveBeenLastCalledWith(firstOption, { notMerge: true });
    expect(runtime.chart.resize).toHaveBeenLastCalledWith({ height: 240, width: 640 });

    view.rerender(<EChartRuntime height={240} option={secondOption} />);
    expect(runtime.chart.setOption).toHaveBeenLastCalledWith(secondOption, { notMerge: true });

    view.rerender(<EChartRuntime height={300} option={secondOption} />);
    expect(runtime.chart.resize).toHaveBeenLastCalledWith({ height: 300, width: 640 });

    window.dispatchEvent(new Event("resize"));
    expect(runtime.chart.resize).toHaveBeenLastCalledWith({ height: 300, width: 640 });
    const resizeCallsBeforeUnmount = runtime.chart.resize.mock.calls.length;

    view.unmount();
    expect(runtime.chart.dispose).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event("resize"));
    expect(runtime.chart.resize).toHaveBeenCalledTimes(resizeCallsBeforeUnmount);
  });
});
