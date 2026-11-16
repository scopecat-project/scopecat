// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { readFile } from "node:fs/promises";
import { compile } from "tailwindcss";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CurrentMeasurement, type CurrentMeasurementResult } from "./CurrentMeasurement";
import { EChart } from "../../ui/EChart";

// Model the previous wide SVG until ResizeObserver runs; do not hide or crop it.
vi.mock("../../ui/EChart", () => ({
  EChart: vi.fn(() => (
    <div data-testid="layout-chart">
      <svg width="640" height="260" />
    </div>
  )),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.querySelectorAll("style[data-layout-contract]").forEach((style) => style.remove());
});
const result: CurrentMeasurementResult = {
  selection: {
    instrument_id: "scope",
    session_id: "session",
    command_id: "layout",
    title: "",
    line_id: "",
    line_label: "",
    note: "",
    series_id: "",
  },
  receivedAt: "2026-01-01T00:00:00Z",
  target: {
    interfaceId: "scope",
    componentPath: [],
    acquisition: {
      id: "capture",
      results: [{ id: "signal", role: "observable", dtype: "float64", unit: "V" }],
    },
  },
};
it("uses real zero-minimum Tailwind grid tracks through the wide-to-narrow result hierarchy", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        method: "exact_slice",
        status: "collected",
        raw_bytes: 16,
        fields: [
          {
            request_id: "signal",
            kind: "array",
            dtype: "float64",
            unit: "V",
            unit_truncated: false,
            shape: [2],
            total_count: 2,
            start: 0,
            returned_count: 2,
            next_start: null,
            complete: true,
            values: [1, 2],
            truncated_indices: [],
            metadata: {},
            metadata_truncated: false,
          },
        ],
        fields_truncated: false,
        metadata: {},
        metadata_truncated: false,
        problems: [],
        problems_truncated: false,
      }),
    ),
  );
  const theme = await readFile(new URL(import.meta.resolve("tailwindcss/theme.css")), "utf8");
  const compiler = await compile(`${theme}\n@tailwind utilities;`);
  const style = document.createElement("style");
  style.dataset.layoutContract = "true";
  style.textContent = compiler.build(["grid", "grid-cols-1", "min-w-0", "max-w-full"]);
  document.head.append(style);
  const view = render(
    <div style={{ width: 1100 }}>
      <CurrentMeasurement result={result} staged={false} />
    </div>,
  );
  await screen.findByTestId("layout-chart");
  for (const width of [1100, 320, 580]) {
    view.rerender(
      <div style={{ width }}>
        <CurrentMeasurement result={result} staged={false} />
      </div>,
    );
    for (const label of ["当前测量结果", "有界结果预览"]) {
      const card = screen.getByRole("region", { name: label });
      expect(getComputedStyle(card).gridTemplateColumns).toBe("repeat(1, minmax(0px, 1fr))");
      expect(getComputedStyle(card).minWidth).toBe("0px");
      expect(getComputedStyle(card).maxWidth).toBe("100%");
      expect(card).not.toHaveClass("overflow-hidden");
    }
    const numericGrid = screen
      .getByText("查看局部原始数值（按窗口）")
      .closest("details")!
      .querySelector(".grid")!;
    expect(getComputedStyle(numericGrid).gridTemplateColumns).toBe("repeat(1, minmax(0px, 1fr))");
    const field = screen.getByTestId("layout-chart").closest("section")!;
    expect(getComputedStyle(field).minWidth).toBe("0px");
    expect(getComputedStyle(field).maxWidth).toBe("100%");
    expect(field).not.toHaveClass("overflow-hidden");
    expect(vi.mocked(EChart).mock.calls.at(-1)![0].option).toMatchObject({
      series: [
        {
          data: [
            [0, 1],
            [1, 2],
          ],
        },
      ],
    });
  }
});
