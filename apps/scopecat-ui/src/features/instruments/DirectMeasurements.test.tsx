// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DirectMeasurements, SavedCurve } from "./DirectMeasurements";
import {
  comparisonGroups,
  conditionDifferences,
  measurementGroupKey,
} from "./MeasurementComparison";
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

it("separates equal labels by identity and retains bounded selections across search and pages", async () => {
  const items = Array.from({ length: 5 }, (_, index) => {
    const record = saved([], []);
    record.id = `r${index}`;
    record.selection.line_id = `line-${index}`;
    return record;
  });
  const older = saved([], []);
  older.id = "older";
  vi.mocked(listDirectMeasurements).mockImplementation(async (cursor) =>
    cursor ? { items: [older] } : { items, next_cursor: 10 },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request | string) => {
      const url = typeof request === "string" ? request : request.url;
      return url.endsWith("/receipt")
        ? new Response(new ArrayBuffer(0))
        : new Response(JSON.stringify(items[0]), {
            headers: { "Content-Type": "application/json" },
          });
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DirectMeasurements pending={[]} onSaved={vi.fn()} instrumentId="scope" onResume={vi.fn()} />
    </QueryClientProvider>,
  );
  await screen.findByRole("checkbox", { name: "选择记录 r0" });
  expect(screen.getAllByRole("group")).toHaveLength(5);
  for (let index = 0; index < 4; index++)
    fireEvent.click(screen.getByRole("checkbox", { name: `选择记录 r${index}` }));
  expect(screen.getByRole("checkbox", { name: "选择记录 r4" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "查找当前页记录" }), {
    target: { value: "not-found" },
  });
  expect(screen.getByText("当前页没有匹配记录。")).toBeVisible();
  expect(screen.getByRole("button", { name: "移除已选记录 r0" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "更早记录" }));
  fireEvent.change(screen.getByRole("textbox", { name: "查找当前页记录" }), {
    target: { value: "" },
  });
  expect(await screen.findByRole("checkbox", { name: "选择记录 older" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "移除已选记录 r0" }));
  expect(screen.getByRole("checkbox", { name: "选择记录 older" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "移除已选记录 r1" })).toBeVisible();
});

it("groups by stable IDs, not names, and never merges unknown historical identities", () => {
  const a = saved([], []);
  const b = structuredClone(a);
  b.id = "second";
  b.selection.line_label = "renamed";
  expect(measurementGroupKey(a)).toBe(measurementGroupKey(b));
  b.selection.series_id = "other-series";
  expect(measurementGroupKey(a)).not.toBe(measurementGroupKey(b));
  b.selection.series_id = a.selection.series_id;
  b.selection.instrument_id = "other-device";
  expect(measurementGroupKey(a)).not.toBe(measurementGroupKey(b));
  b.selection.instrument_id = a.selection.instrument_id;
  a.selection.line_id = b.selection.line_id = "";
  expect(measurementGroupKey(a)).not.toBe(measurementGroupKey(b));
});

it("overlays actual unequal grids without interpolation and preserves invalid samples", () => {
  const a = saved([], []);
  const b = structuredClone(a);
  b.id = "second";
  const other = structuredClone(receipt);
  const x = other.readback!.values!.x!;
  const y = other.readback!.values!.signal!;
  if (x.kind !== "array" || y.kind !== "array") throw new Error("fixture");
  x.values = [12, 40];
  y.values = [8, NaN];
  const result = comparisonGroups(
    [
      { record: a, acquisition, receipt },
      { record: b, acquisition, receipt: other },
    ],
    "magnitude",
  );
  expect(result.excluded).toEqual([]);
  expect(result.groups).toHaveLength(1);
  expect(result.groups[0]!.curves.map((curve) => curve.data)).toEqual([
    [
      [10, 3],
      [20, 4],
    ],
    [
      [12, 8],
      [40, null],
    ],
  ]);
});

it.each([
  "instrument",
  "interface",
  "component",
  "unit",
  "contract",
  "missing-coordinate",
  "missing-unit",
  "shape",
])("keeps %s mismatches in independent views", (kind) => {
  const a = saved([], []);
  const b = structuredClone(a);
  b.id = "second";
  const other = structuredClone(receipt);
  const contract = structuredClone(acquisition);
  if (kind === "instrument") b.selection.instrument_id = "different";
  if (kind === "interface") b.intent.interface_id = "other.scope/v1";
  if (kind === "component") b.intent.component_path = ["channel", "other"];
  const y = other.readback!.values!.signal!;
  if (y.kind !== "array") throw new Error("fixture");
  if (kind === "unit") y.unit = "A";
  if (kind === "missing-unit") y.unit = null;
  if (kind === "shape") y.shape = [20];
  if (kind === "contract") contract.results[1]!.axes![0]!.kind = "time";
  if (kind === "missing-coordinate") delete other.readback!.values!.x;
  const result = comparisonGroups(
    [
      { record: a, acquisition, receipt },
      { record: b, acquisition: contract, receipt: other },
    ],
    "magnitude",
  );
  expect(result.groups).toEqual([]);
  expect(result.excluded).toHaveLength(2);
});

it("identifies condition evidence differences without treating command IDs as changed settings", () => {
  const a = saved([], []);
  const b = structuredClone(a);
  b.intent.command_id = "second-command";
  expect(conditionDifferences(b, a, receipt, receipt)).toEqual([]);
  b.setup.content_hash = "new-hash";
  const other = structuredClone(receipt);
  other.metadata = { source: "stored trace" };
  expect(conditionDifferences(b, a, other, receipt)).toEqual(["设置快照", "回执生成证据"]);
});

it("preserves complex representation units and coordinate availability gaps in overlays", () => {
  const a = saved([], []);
  const b = structuredClone(a);
  b.id = "second";
  const complex = structuredClone(receipt);
  const contract = structuredClone(acquisition);
  contract.results[1]!.dtype = "complex128";
  const y = complex.readback!.values!.signal!;
  const x = complex.readback!.values!.x!;
  if (y.kind !== "array" || x.kind !== "array") throw new Error("fixture");
  y.dtype = "complex128";
  y.values = [
    { real: 0, imag: 2 },
    { real: 3, imag: 4 },
  ];
  x.values = [10, NaN];
  const entries = [
    { record: a, acquisition: contract, receipt: complex },
    { record: b, acquisition: contract, receipt: complex },
  ];
  const phase = comparisonGroups(entries, "phase");
  expect(phase.groups[0]!.yLabel).toBe("signal (rad)");
  expect(phase.groups[0]!.curves[0]!.data).toEqual([
    [10, Math.PI / 2],
    [null, Math.atan2(4, 3)],
  ]);
  expect(comparisonGroups(entries, "magnitude").groups[0]!.curves[0]!.data).toEqual([
    [10, 2],
    [null, 5],
  ]);
});

it("allows only the explicit sweep/retained-trace family with compatible complete result contracts", () => {
  const sweep = saved([], []);
  sweep.intent.interface_id = "scopecat.network_sweep/v1";
  sweep.intent.acquisition_id = "sweep";
  const trace = structuredClone(sweep);
  trace.id = "trace";
  trace.intent.interface_id = "scopecat.existing_network_trace/v1";
  trace.intent.acquisition_id = "read_trace";
  const axis = {
    id: "frequency",
    kind: "frequency",
    size: null,
    unit: "Hz",
    coordinate_result: "frequency",
  };
  const sweepContract: InstrumentAcquisition = {
    id: "sweep",
    results: [
      {
        id: "frequency",
        dtype: "float64",
        role: "coordinate",
        unit: "Hz",
        axes: [{ ...axis, size: 2 }],
      },
      {
        id: "s_parameter",
        dtype: "complex128",
        role: "observable",
        unit: "ratio",
        axes: [{ ...axis, size: 2 }],
      },
    ],
  };
  const traceContract = structuredClone(sweepContract);
  traceContract.id = "read_trace";
  traceContract.results.forEach((result) => {
    result.axes![0]!.size = null;
  });
  const networkReceipt: InstrumentCollectReceipt = {
    status: "collected",
    problems: [],
    readback: {
      values: {
        frequency: { kind: "array", dtype: "float64", shape: [2], unit: "Hz", values: [1e9, 2e9] },
        s_parameter: {
          kind: "array",
          dtype: "complex128",
          shape: [2],
          unit: "ratio",
          values: [
            { real: 1, imag: 0 },
            { real: 0, imag: 1 },
          ],
        },
      },
    },
  };
  const entries = [
    { record: sweep, acquisition: sweepContract, receipt: networkReceipt },
    { record: trace, acquisition: traceContract, receipt: networkReceipt },
  ];
  expect(comparisonGroups(entries, "magnitude").groups).toHaveLength(1);
  expect(conditionDifferences(trace, sweep)).toContain("采集来源");
  trace.intent.interface_id = "custom.same-result-names/v1";
  expect(comparisonGroups(entries, "magnitude").groups).toHaveLength(0);
  trace.intent.interface_id = "scopecat.existing_network_trace/v1";
  traceContract.results[1]!.description = "different physical meaning";
  expect(comparisonGroups(entries, "magnitude").groups).toHaveLength(0);
  delete traceContract.results[1]!.description;
  traceContract.results[1]!.axes![0]!.unit = "s";
  expect(comparisonGroups(entries, "magnitude").groups).toHaveLength(0);
});

it.each([false, true])(
  "preserves saved receipt metadata with explicit hideHeading=%s independently of readOnly",
  async (hideHeading) => {
    const record = saved([], []);
    record.selection.title = "Retained title";
    record.selection.note = "Original note";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request | string) =>
        (typeof request === "string" ? request : request.url).endsWith("/receipt")
          ? new Response(new ArrayBuffer(0))
          : Response.json({
              ...record,
              notes: [
                {
                  operation_id: "note-1",
                  note: "Retained annotation",
                  anomalous: false,
                  recorded_at: "2026-01-02T00:00:00Z",
                },
              ],
            }),
      ),
    );
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <SavedCurve record={record} readOnly hideHeading={hideHeading} />
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("chart")).toHaveTextContent('"data":[[10,3],[20,4]]');
    expect(await screen.findByText(/Retained annotation/)).toBeVisible();
    expect(screen.queryAllByRole("heading")).toHaveLength(hideHeading ? 0 : 1);
    expect(
      screen.getByText(hideHeading ? "Original note" : "Retained title · Original note"),
    ).toBeVisible();
    expect(screen.queryByRole("heading")?.textContent ?? "").toBe(
      hideHeading ? "" : "Retained title · Original note",
    );
    expect(screen.getByText(/record ·/)).toHaveTextContent(
      new Date(record.collected_at).toLocaleString(),
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "追加标记（不修改原结果）" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("设备、线路和设置来源")).toBeVisible();
  },
);
