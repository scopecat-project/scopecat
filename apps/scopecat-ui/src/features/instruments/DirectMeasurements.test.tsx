// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DirectMeasurements } from "./DirectMeasurements";
import { listDirectMeasurements, type SavedMeasurement } from "./direct-measurement-api";
import type { InstrumentAcquisition, InstrumentCollectReceipt } from "../../api-contract";

vi.mock("./direct-measurement-api", () => ({
  listDirectMeasurements: vi.fn(),
  saveDirectMeasurement: vi.fn(),
}));
vi.mock("./hardware-receipt-wire", () => ({ decodeCollectReceipt: () => receipt }));
vi.mock("../../ui/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <pre data-testid="chart">{JSON.stringify(option)}</pre>
  ),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
const receipt: InstrumentCollectReceipt = {
  status: "collected",
  problems: [],
  readback: {
    values: {
      x: { kind: "array", dtype: "float64", shape: [2], values: [10, 20], unit: "Hz" },
      signal: { kind: "array", dtype: "float64", shape: [2], values: [3, 4], unit: "V" },
    },
  },
};
function saved(mount: string[], nested: string[]): SavedMeasurement {
  return {
    id: "record",
    saved_at: "2026-01-01T00:00:00Z",
    collected_at: "2026-01-01T00:00:00Z",
    selection: {
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
async function reopen(record: SavedMeasurement) {
  vi.mocked(listDirectMeasurements).mockResolvedValue({ items: [record] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request | string) => {
      const url = typeof request === "string" ? request : request.url;
      return url.endsWith("/receipt")
        ? new Response(new ArrayBuffer(0))
        : new Response(JSON.stringify(record), { headers: { "Content-Type": "application/json" } });
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DirectMeasurements pending={[]} onSaved={vi.fn()} instrumentId="scope" onResume={vi.fn()} />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("checkbox"));
}
it.each([
  [[], ["channels", "2"]],
  [["rack", "scope"], []],
  [
    ["rack", "scope"],
    ["channels", "2"],
  ],
])("reopens the exact stored acquisition at mount %j and nested path %j", async (mount, nested) => {
  await reopen(saved(mount, nested));
  expect(await screen.findByTestId("chart")).toHaveTextContent('"data":[[10,3],[20,4]]');
  expect(screen.getByText("完整回执与采集证据（含复数原值）")).toBeVisible();
});
it("keeps the receipt accessible when its recorded component cannot be resolved", async () => {
  const record = saved(["rack", "scope"], ["channels", "2"]);
  record.intent.component_path = ["other", "channels", "2"];
  await reopen(record);
  expect(await screen.findByText(/无法解析保存的采集契约/)).toBeVisible();
  expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
  const summary = screen.getByText("完整回执与采集证据（含复数原值）");
  const details = summary.closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(details).toHaveTextContent('"signal"');
});
