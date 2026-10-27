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
}: {
  ariaLabel: string;
  description?: string;
  height: number;
  option: EChartsCoreOption;
  pointCount: number;
  seriesLabels: string[];
  seriesCount: number;
  onPoint?: (point: { seriesId: string; dataIndex: number }) => void;
}) {
  const descriptionId = useId();

  return (
    <>
      <div aria-describedby={descriptionId} aria-label={ariaLabel} role="img">
        <Suspense fallback={<div aria-hidden="true" style={{ height }} />}>
          <EChartRuntime height={height} option={option} onPoint={onPoint} />
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
