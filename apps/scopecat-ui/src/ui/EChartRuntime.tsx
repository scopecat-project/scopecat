import { useEffect, useRef } from "react";
import { CustomChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomInsideComponent,
  GridComponent,
  LegendScrollComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
} from "echarts/components";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";

use([
  CustomChart,
  DataZoomInsideComponent,
  GridComponent,
  LegendScrollComponent,
  LineChart,
  ScatterChart,
  SVGRenderer,
  TooltipComponent,
  VisualMapContinuousComponent,
]);

export function EChartRuntime({
  height,
  option,
  onPoint,
}: {
  height: number;
  option: EChartsCoreOption;
  onPoint?: (point: { seriesId: string; dataIndex: number }) => void;
}) {
  const elementRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof init> | null>(null);
  const heightRef = useRef(height);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const chart = init(element, undefined, {
      height: element.clientHeight || heightRef.current,
      renderer: "svg",
      width: element.clientWidth || 640,
    });
    chartRef.current = chart;

    const resize = () => {
      chart.resize({
        height: element.clientHeight || heightRef.current,
        width: element.clientWidth || 640,
      });
    };
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(resize);
    observer?.observe(element);
    if (!observer) window.addEventListener("resize", resize);

    return () => {
      observer?.disconnect();
      if (!observer) window.removeEventListener("resize", resize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onPoint) return;
    const click = (event: unknown) => {
      if (
        typeof event !== "object" ||
        event === null ||
        !("seriesId" in event) ||
        !("dataIndex" in event)
      )
        return;
      if (typeof event.seriesId === "string" && typeof event.dataIndex === "number")
        onPoint({ seriesId: event.seriesId, dataIndex: event.dataIndex });
    };
    chart.on("click", click);
    return () => {
      chart.off("click", click);
    };
  }, [onPoint]);

  useEffect(() => {
    heightRef.current = height;
    chartRef.current?.resize({
      height: elementRef.current?.clientHeight || height,
      width: elementRef.current?.clientWidth || 640,
    });
  }, [height]);

  return <div aria-hidden="true" ref={elementRef} style={{ height, width: "100%" }} />;
}
