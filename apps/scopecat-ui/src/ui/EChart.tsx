import type { ChartInteractionStore } from "./chart-interaction";
import { Component, lazy, Suspense, useId, type ReactNode } from "react";
import { loadChartRuntime } from "./chart-runtime-loader";
import { secondaryButton } from "./styles";
import type { EChartsCoreOption } from "echarts/core";

// Keep the lazy type stable through ordinary renders. An explicit retry creates
// a new type because React caches a rejected promise on the old lazy type.
function createRuntime() {
  return lazy(loadChartRuntime);
}

let CurrentRuntime = createRuntime();
function retryRuntime() {
  CurrentRuntime = createRuntime();
  return CurrentRuntime;
}

class ChartBoundary extends Component<
  {
    height: number | "fill";
    children: (Runtime: ReturnType<typeof createRuntime>) => ReactNode;
  },
  { failed: boolean; Runtime: ReturnType<typeof createRuntime> }
> {
  override state = { failed: false, Runtime: CurrentRuntime };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override render() {
    if (this.state.failed)
      return (
        <div
          role="alert"
          className="min-w-0 overflow-auto p-3 text-sm text-text-soft"
          style={{ height: this.props.height === "fill" ? "100%" : this.props.height }}
        >
          <p>图表暂时无法显示。原结果仍由外层页面保留，可继续查看数据或保存。</p>
          <button
            type="button"
            className={secondaryButton}
            onClick={() => this.setState({ failed: false, Runtime: retryRuntime() })}
          >
            重试图表
          </button>
          <p className="mt-2 text-xs">重试只加载图表，不会重新采集或重新连接设备。</p>
        </div>
      );
    return this.props.children(this.state.Runtime);
  }
}

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
      <ChartBoundary height={height}>
        {(EChartRuntime) => (
          <Suspense
            fallback={
              <>
                <div role="img" aria-label={ariaLabel} aria-describedby={descriptionId} />
                <div
                  role="status"
                  className="p-3 text-sm text-text-soft"
                  style={{ height: height === "fill" ? "100%" : height }}
                >
                  正在加载图表…
                </div>
              </>
            }
          >
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
              <EChartRuntime
                height={height}
                option={option}
                onPoint={onPoint}
                interactionKey={interactionKey}
                interactionStore={interactionStore}
                resetRange={resetRange}
              />
            </div>
          </Suspense>
        )}
      </ChartBoundary>
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
