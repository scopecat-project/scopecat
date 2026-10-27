import type { ChartInteraction, ChartInteractionStore } from "./chart-interaction";
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
  interactionKey,
  interactionStore,
  resetRange = 0,
}: {
  // Opt in only with a key covering the measurement domain, signal and axis units.
  interactionKey?: string;
  interactionStore?: ChartInteractionStore;
  resetRange?: number;
  height: number;
  option: EChartsCoreOption;
  onPoint?: (point: { seriesId: string; dataIndex: number }) => void;
}) {
  const elementRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof init> | null>(null);
  const heightRef = useRef(height);
  const retained = useRef<ChartInteraction & { key?: string }>({});
  const previousReset = useRef(resetRange);

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
    const chart = chartRef.current;
    if (!chart) return;
    if (retained.current.key !== interactionKey) {
      retained.current = {
        key: interactionKey,
        ...(interactionKey === undefined ? undefined : interactionStore?.read(interactionKey)),
      };
    }
    if (previousReset.current !== resetRange) {
      retained.current.zoom = undefined;
      previousReset.current = resetRange;
      if (interactionKey !== undefined) interactionStore?.write(interactionKey, retained.current);
    }
    const saved = retained.current;
    chart.setOption(
      interactionKey === undefined
        ? option
        : {
            ...option,
            dataZoom: Array.isArray(option.dataZoom)
              ? option.dataZoom.map((zoom, index) =>
                  saved.zoom?.[index]
                    ? { ...zoom, ...saved.zoom[index], rangeMode: ["value", "value"] }
                    : zoom,
                )
              : option.dataZoom,
            legend: saved.selected
              ? { ...((option.legend ?? {}) as Record<string, unknown>), selected: saved.selected }
              : option.legend,
          },
      { notMerge: true },
    );
  }, [option, interactionKey, resetRange, interactionStore]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || interactionKey === undefined) return;
    const zoom = () => {
      const current = (chart.getOption().dataZoom ?? []) as Array<{
        start: number;
        end: number;
        startValue: number;
        endValue: number;
      }>;
      // Percentages drift as the source extent grows. Keep the resolved coordinates.
      retained.current.zoom = current.map((range) =>
        range.start === 0 && range.end === 100
          ? undefined
          : {
              startValue: range.startValue,
              endValue: range.endValue,
            },
      );
      interactionStore?.write(interactionKey, retained.current);
    };
    const legend = () => {
      const current = chart.getOption().legend as Array<{ selected: Record<string, boolean> }>;
      retained.current.selected = { ...current[0]?.selected };
      zoom();
    };
    chart.on("datazoom", zoom);
    chart.on("legendselectchanged", legend);
    return () => {
      chart.off("datazoom", zoom);
      chart.off("legendselectchanged", legend);
    };
  }, [interactionKey, interactionStore]);

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
