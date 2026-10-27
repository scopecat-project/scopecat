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
    getOption: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
  };
  return {
    chart,
    init: vi.fn(() => chart),
    use: vi.fn(),
  };
});

vi.mock("echarts/core", () => ({ init: runtime.init, use: runtime.use }));

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

it("retains value ranges and legend selection only within the opted-in measurement identity", () => {
  const option = {
    dataZoom: [
      { type: "inside", xAxisIndex: 0 },
      { type: "inside", yAxisIndex: 0 },
    ],
    legend: { data: ["A", "B"] },
    series: [
      {
        id: "a",
        type: "line",
        data: [
          [0, 0],
          [10, 10],
        ],
      },
    ],
  };
  const view = render(<EChartRuntime height={240} option={option} interactionKey="run:signal:V" />);
  runtime.chart.getOption.mockReturnValue({
    dataZoom: [
      { start: 20, end: 60, startValue: 2, endValue: 6 },
      { start: 0, end: 100, startValue: 0, endValue: 10 },
    ],
    legend: [{ selected: { B: false } }],
  });
  const emit = (event: string) =>
    runtime.chart.on.mock.calls
      .filter(([name]) => name === event)
      .at(-1)![1]();
  emit("datazoom");
  emit("legendselectchanged");
  const appended = {
    ...option,
    series: [
      {
        ...option.series[0],
        data: [
          [0, 0],
          [20, 20],
        ],
      },
    ],
  };
  view.rerender(<EChartRuntime height={240} option={appended} interactionKey="run:signal:V" />);
  expect(runtime.chart.setOption).toHaveBeenLastCalledWith(
    expect.objectContaining({
      dataZoom: [
        expect.objectContaining({ startValue: 2, endValue: 6, rangeMode: ["value", "value"] }),
        option.dataZoom[1],
      ],
      legend: { data: ["A", "B"], selected: { B: false } },
      series: appended.series,
    }),
    { notMerge: true },
  );
  view.rerender(
    <EChartRuntime height={240} option={appended} interactionKey="run:signal:V" resetRange={1} />,
  );
  expect(runtime.chart.setOption).toHaveBeenLastCalledWith(
    expect.objectContaining({
      dataZoom: option.dataZoom,
      legend: { data: ["A", "B"], selected: { B: false } },
    }),
    { notMerge: true },
  );
  emit("datazoom");
  view.rerender(
    <EChartRuntime height={240} option={appended} interactionKey="run:signal:mV" resetRange={1} />,
  );
  expect(runtime.chart.setOption).toHaveBeenLastCalledWith(appended, { notMerge: true });
  expect(runtime.chart.off).toHaveBeenCalledWith("datazoom", expect.any(Function));
});
