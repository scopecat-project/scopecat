// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { comparisonGroups, MeasurementComparison } from "./MeasurementComparison";
import {
  readMeasurementPreview,
  type MeasurementPreview,
  type PreviewField,
} from "./measurement-preview";
import type { SavedMeasurement } from "./direct-measurement-api";
import type { InstrumentAcquisition } from "../../api-contract";
vi.mock("./measurement-preview", () => ({
  readMeasurementPreview: vi.fn(),
  useMeasurementPreview: vi.fn(),
}));
vi.mock("../../ui/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <pre data-testid="comparison-chart">{JSON.stringify(option)}</pre>
  ),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const acquisition: InstrumentAcquisition = {
  id: "capture",
  results: [
    { id: "x", dtype: "float64", role: "coordinate", axes: [] },
    {
      id: "signal",
      role: "observable",
      dtype: "float64",
      axes: [{ id: "x", kind: "frequency", size: null, coordinate_result: "x" }],
    },
  ],
};
function saved(mount: string[], nested: string[]): SavedMeasurement {
  return {
    id: "record",
    saved_at: "2026-01-01T00:00:00Z",
    receipt_validation: "verified" as const,
    collected_at: "2026-01-01T00:00:00Z",
    selection: {
      title: "",
      session_id: "closed",
      instrument_id: "scope",
      command_id: "capture",
      line_label: "line-a",
      line_id: "line-a",
      series_id: "series",
      note: "",
    },
    setup: { revision_id: "old", content_hash: "old-hash" },
    observed_before_collection: null,
    receipt_digest: "digest",
    intent: {
      retain_result: true,
      instrument_id: "scope",
      command_id: "capture",
      interface_id: "test.scope/v1",
      acquisition_id: "capture",
      component_path: [...mount, ...nested],
    },
    description: {
      instrument_id: "scope",
      implementation_id: "test",
      implementation_version: "1",
      interface_mounts: mount.length
        ? [{ interface_id: "test.scope/v1", component_path: mount }]
        : [],
      interfaces: [
        {
          id: "test.scope/v1",
          acquisitions: nested.length ? [] : [acquisition],
          components: nested.length
            ? [{ id: "channels", components: [{ id: "2", acquisitions: [acquisition] }] }]
            : [],
        },
      ],
    },
  };
}

function preview(start = 0): MeasurementPreview {
  const fields: PreviewField[] = [
    {
      request_id: "x",
      kind: "array",
      dtype: "float64",
      unit: "Hz",
      unit_truncated: false,
      shape: [100],
      total_count: 100,
      start,
      returned_count: 2,
      next_start: start + 2,
      complete: false,
      values: [10 + start, 20 + start],
      truncated_indices: [],
      metadata: {},
      metadata_truncated: false,
    },
    {
      request_id: "signal",
      kind: "array",
      dtype: "float64",
      unit: "V",
      unit_truncated: false,
      shape: [100],
      total_count: 100,
      start,
      returned_count: 2,
      next_start: start + 2,
      complete: false,
      values: [3, 4],
      truncated_indices: [],
      metadata: {},
      metadata_truncated: false,
    },
  ];
  return {
    method: "exact_slice",
    status: "collected",
    raw_bytes: 1600,
    fields,
    fields_truncated: false,
    metadata: {},
    metadata_truncated: false,
    problems: [],
    problems_truncated: false,
  };
}
function entries(a = preview(), b = preview()) {
  const first = saved([], []);
  const second = { ...structuredClone(first), id: "second" };
  return [
    { record: first, acquisition, preview: a },
    { record: second, acquisition, preview: b },
  ];
}
it("compares exact aligned partial windows without inventing a complete shape", () => {
  const source = preview(12);
  const result = comparisonGroups(entries(source), "magnitude");
  expect(result.groups).toHaveLength(1);
  expect(result.groups[0]!.curves[0]!.data).toEqual([
    [22, 3],
    [32, 4],
  ]);
  expect(result.groups[0]!.curves[0]!.label).toContain("窗口 13–14/100");
  expect(source.fields[0]!.shape).toEqual([100]);
  expect(source.fields[0]!.complete).toBe(false);
});
it.each(["start", "total", "unit-truncated", "cells-truncated", "count"])(
  "rejects incompatible partial %s evidence",
  (kind) => {
    const other = preview();
    const y = other.fields[1]!;
    if (kind === "start") y.start = 3;
    if (kind === "total") {
      y.total_count = 101;
      y.shape = [101];
    }
    if (kind === "unit-truncated") y.unit_truncated = true;
    if (kind === "cells-truncated") y.truncated_indices = [0];
    if (kind === "count") y.returned_count = 3;
    expect(comparisonGroups(entries(preview(), other), "magnitude").groups).toEqual([]);
  },
);
it("does not coerce unsafe int64 coordinates into a misleading curve", () => {
  const data = preview();
  data.fields[0]!.dtype = "int64";
  data.fields[0]!.values = ["9223372036854775807", "9223372036854775806"];
  const contract = structuredClone(acquisition);
  contract.results[0]!.dtype = "int64";
  expect(
    comparisonGroups(
      entries(data, data).map((entry) => ({ ...entry, acquisition: contract })),
      "real",
    ).groups,
  ).toEqual([]);
});
it("uses only bounded preview requests, displays partial scope and follows actual next offsets", async () => {
  vi.mocked(readMeasurementPreview).mockImplementation(async (_source, start) => preview(start));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MeasurementComparison entries={entries()} />
    </QueryClientProvider>,
  );
  expect(await screen.findByTestId("comparison-chart")).toBeVisible();
  expect(screen.getByText(/未显示范围的峰值与差异未知/)).toBeVisible();
  expect(screen.getByText(/record：x 1–2\/100/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "比较下一段" }));
  await waitFor(() =>
    expect(vi.mocked(readMeasurementPreview).mock.calls.some((call) => call[1] === 2)).toBe(true),
  );
  expect(await screen.findByText(/record：x 3–4\/100/)).toBeVisible();
  expect(vi.mocked(readMeasurementPreview).mock.calls.every((call) => "recordId" in call[0])).toBe(
    true,
  );
  client.clear();
});

it("allows retry of one failed preview without discarding another record", async () => {
  vi.mocked(readMeasurementPreview)
    .mockRejectedValueOnce(new Error("temporary preview failure"))
    .mockImplementation(async (_source, start) => preview(start));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MeasurementComparison entries={entries()} />
    </QueryClientProvider>,
  );
  const retry = await screen.findByRole("button", { name: "重试比较记录 record" });
  expect(screen.getByText(/second：x 1–2\/100/)).toBeVisible();
  fireEvent.click(retry);
  expect(await screen.findByTestId("comparison-chart")).toBeVisible();
  client.clear();
});
