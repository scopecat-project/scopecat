// @vitest-environment node
import { renderToString } from "react-dom/server";
import { init } from "echarts/core";
import { expect, it, vi } from "vitest";
import "../../ui/EChartRuntime";
import { EChart } from "../../ui/EChart";
import { BoundedMeasurement, previewNumbers, type Representation } from "./BoundedMeasurement";
import {
  useMeasurementPreview,
  type MeasurementPreview,
  type PreviewField,
} from "./measurement-preview";

vi.mock("../../ui/EChart", () => ({ EChart: vi.fn(() => null) }));
vi.mock("./measurement-preview", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./measurement-preview")>()),
  useMeasurementPreview: vi.fn(),
}));
function fixture(points: number): MeasurementPreview {
  const count = Math.min(points, 512);
  const common = {
    kind: "array" as const,
    unit_truncated: false,
    shape: [points],
    total_count: points,
    start: 0,
    returned_count: count,
    next_start: count < points ? count : null,
    complete: count === points,
    truncated_indices: [],
    metadata: {},
    metadata_truncated: false,
  };
  const fields: PreviewField[] = [
    {
      ...common,
      request_id: "frequency",
      dtype: "float64",
      unit: "Hz",
      values: Array.from({ length: count }, (_, index) => 5e9 + (index * 1e6) / (points - 1)),
    },
    {
      ...common,
      request_id: "signal",
      dtype: "complex128",
      unit: "V",
      values: Array.from({ length: count }, (_, index) => ({
        real: 0.5 + index / 1000,
        imag: Math.sin(index / 20) / 10,
      })),
    },
  ];
  return {
    method: "exact_slice",
    status: "collected",
    raw_bytes: 8000,
    fields,
    fields_truncated: false,
    metadata: {},
    metadata_truncated: false,
    problems: [],
    problems_truncated: false,
  };
}
function xTicks(svg: string) {
  return [...svg.matchAll(/<text[^>]*text-anchor="middle"[^>]*>([^<]+)<\/text>/g)]
    .map((match) => match[1]!)
    .filter((text) => /^-?[\d,.]+(?:e[+-]?\d+)?$/.test(text));
}
it.each(["magnitude", "phase", "real", "imag"] as const)(
  "renders distinct GHz-offset ticks and usable plot geometry for %s without changing coordinates",
  (representation: Representation) => {
    for (const points of [201, 1025]) {
      const data = fixture(points);
      vi.mocked(useMeasurementPreview).mockReturnValue({
        data,
        error: undefined,
        isPending: false,
        retry: vi.fn(),
      });
      renderToString(
        <BoundedMeasurement
          source={{ recordId: "ssr-curve" }}
          displayStore={
            new Map([["saved:ssr-curve", { start: 0, representation, zoom: new Map() }]])
          }
          acquisition={{
            id: "capture",
            results: [
              { id: "frequency", role: "coordinate", dtype: "float64", unit: "Hz" },
              {
                id: "signal",
                label: "Long display name must not consume the narrow plot",
                role: "observable",
                dtype: "complex128",
                unit: "V",
                axes: [
                  {
                    id: "frequency",
                    kind: "frequency",
                    coordinate_result: "frequency",
                    size: points,
                    unit: "Hz",
                  },
                ],
              },
            ],
          }}
        />,
      );
      const option = vi.mocked(EChart).mock.calls.at(-1)![0].option;
      const x = previewNumbers(data.fields[0]!, "real");
      const y = previewNumbers(data.fields[1]!, representation);
      const scientificData = x.map((value, index) => [value, y[index]]);
      expect(option).toMatchObject({
        tooltip: { trigger: "axis" },
        series: [{ data: scientificData }],
      });
      const chart = init(null, undefined, { renderer: "svg", ssr: true, width: 260, height: 260 });
      try {
        chart.setOption(option);
        for (const width of [250, 260, 270, 390, 700, 260]) {
          chart.resize({ width, height: 260 });
          const svg = chart.renderToSVGString();
          const ticks = xTicks(svg);
          expect(
            new Set(ticks).size,
            `${points} points at ${width}px: ${ticks.join(", ")}`,
          ).toBeGreaterThanOrEqual(2);
          expect(new Set(ticks.map(Number)).size).toBe(ticks.length);
          const plot = svg.match(
            /<clipPath[^>]*>\s*<path d="M[-.\d]+ [-.\d]+l([.\d]+) 0l0 ([.\d]+)/,
          );
          expect(plot).not.toBeNull();
          expect(Number(plot![1])).toBeGreaterThanOrEqual(140);
          expect(Number(plot![2])).toBeGreaterThanOrEqual(100);
          expect(chart.getOption()).toMatchObject({
            xAxis: [
              { name: width <= 420 ? "" : "frequency (Hz)", splitNumber: width <= 420 ? 2 : 3 },
            ],
            series: [{ data: scientificData }],
          });
        }
        // The same precision rule must remain usable after zoom, not only at full extent.
        chart.dispatchAction({ type: "dataZoom", start: 25, end: 75 });
        const zoomed = xTicks(chart.renderToSVGString());
        expect(new Set(zoomed).size).toBeGreaterThanOrEqual(2);
        expect(new Set(zoomed.map(Number)).size).toBe(zoomed.length);
        expect(chart.getOption()).toMatchObject({ series: [{ data: scientificData }] });
      } finally {
        chart.dispose();
      }
    }
  },
);
