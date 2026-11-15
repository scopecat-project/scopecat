import type { ChartInteractionStore } from "./chart-interaction";
import { lazy, Suspense, useId } from "react";
import type { EChartsCoreOption } from "echarts/core";

const EChartRuntime = lazy(async () => {
  const module = await import("./EChartRuntime");
  return { default: module.EChartRuntime };
});

export function EChart({
  ariaLabel,
  description,
  height,
  option,
  pointCount,
  seriesLabels,
  seriesCount,
  onPoint,
  interactionKey,
  interactionStore,
  resetRange,
}: {
  interactionKey?: string;
  interactionStore?: ChartInteractionStore;
  resetRange?: number;
  ariaLabel: string;
  description?: string;
  height: number | "fill";
  option: EChartsCoreOption;
  pointCount: number;
  seriesLabels: string[];
  seriesCount: number;
  onPoint?: (point: { seriesId: string; dataIndex: number }) => void;
}) {
  const descriptionId = useId();

  return (
    <>
      <div
        aria-describedby={descriptionId}
        aria-label={ariaLabel}
        role="img"
        style={
          height === "fill"
            ? { height: "100%", minHeight: 0, position: "relative", overflow: "hidden" }
            : undefined
        }
      >
        <Suspense
          fallback={
            <div aria-hidden="true" style={{ height: height === "fill" ? "100%" : height }} />
          }
        >
          <EChartRuntime
            height={height}
            option={option}
            onPoint={onPoint}
            interactionKey={interactionKey}
            interactionStore={interactionStore}
            resetRange={resetRange}
          />
        </Suspense>
      </div>
      <span className="sr-only" id={descriptionId}>
        {description ?? (
          <>
            {seriesCount.toLocaleString()} 个数据系列，共绘制 {pointCount.toLocaleString()} 个点。
            {seriesLabels.length > 0 ? ` 系列：${seriesLabels.join("、")}。` : ""}
          </>
        )}
      </span>
    </>
  );
}

export type { EChartsCoreOption };
