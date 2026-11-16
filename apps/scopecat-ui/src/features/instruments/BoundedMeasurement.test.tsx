// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EChart } from "../../ui/EChart";
import { BoundedMeasurement, formatPreviewTick } from "./BoundedMeasurement";
import { readMeasurementPreview } from "./measurement-preview";
vi.mock("../../ui/EChart", () => ({ EChart: vi.fn(() => <div data-testid="bounded-chart" />) }));
async function openNumericTable() {
  const details = (await screen.findByText("查看局部原始数值（按窗口）")).closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows exact unsafe int64 strings without loading full receipts", async () => {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      requests.push(url.pathname);
      return Response.json({
        method: "exact_slice",
        status: "collected",
        raw_bytes: 8000000,
        fields: [
          {
            request_id: "counts",
            kind: "array",
            dtype: "int64",
            unit: null,
            unit_truncated: false,
            shape: [1000000],
            total_count: 1000000,
            start: 0,
            returned_count: 1,
            next_start: 1,
            complete: false,
            values: ["9007199254740993"],
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
      });
    }),
  );
  render(<BoundedMeasurement source={{ recordId: "integer-window" }} />);
  expect(await screen.findByText("9007199254740993")).not.toBeVisible();
  await openNumericTable();
  expect(screen.getByText("9007199254740993")).toBeVisible();
  expect(screen.getByLabelText("局部原始数值表")).toHaveClass("max-h-72", "overflow-auto");
  expect(screen.getByLabelText("局部窗口导航")).toHaveClass("flex", "flex-wrap");
  expect(requests).toEqual(["/api/v1/direct-measurements/integer-window/preview"]);
  expect(screen.getByText(/不是全局概览/)).toBeVisible();
});
it("rejects oversized responses before JSON materialization", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("x".repeat(256 * 1024 + 1))),
  );
  await expect(
    readMeasurementPreview({ recordId: "oversize-test" }, 0, new AbortController().signal),
  ).rejects.toThrow("安全大小");
});
it("does not start a queued request after cancellation", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  controller.abort();
  await expect(
    readMeasurementPreview({ recordId: "cancelled-test" }, 0, controller.signal),
  ).rejects.toThrow(/abort/i);
  expect(fetch).not.toHaveBeenCalled();
});

it("keeps representation and pagination independently of payload eviction or selection", async () => {
  const { waitFor } = await import("@testing-library/react");
  const store = new Map();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: URL, init: RequestInit) => {
      const { start } = JSON.parse(init.body as string);
      return Response.json({
        method: "exact_slice",
        status: "collected",
        raw_bytes: 1000,
        fields: [
          {
            request_id: "x",
            kind: "array",
            dtype: "float64",
            unit: null,
            unit_truncated: false,
            shape: [100],
            total_count: 100,
            start,
            returned_count: 2,
            next_start: start + 2,
            complete: false,
            values: [start, start + 1],
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
      });
    }),
  );
  const view = render(<BoundedMeasurement source={{ recordId: "state-a" }} displayStore={store} />);
  await waitFor(() => expect(screen.getByText("下一页数据")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("复数表示"), { target: { value: "phase" } });
  await openNumericTable();
  fireEvent.click(screen.getByText("下一页数据"));
  await waitFor(() => expect(store.get("saved:state-a").start).toBe(2));
  view.rerender(<BoundedMeasurement source={{ recordId: "state-b" }} displayStore={store} />);
  expect(screen.getByLabelText("复数表示")).toHaveValue("magnitude");
  expect(
    (await screen.findByText("查看局部原始数值（按窗口）")).closest("details"),
  ).not.toHaveAttribute("open");
  view.rerender(<BoundedMeasurement source={{ recordId: "state-a" }} displayStore={store} />);
  expect(screen.getByLabelText("复数表示")).toHaveValue("phase");
  expect(
    (await screen.findByText("查看局部原始数值（按窗口）")).closest("details"),
  ).toHaveAttribute("open");
  expect(store.get("saved:state-a").start).toBe(2);
});

const response = (label: string) =>
  Response.json({
    method: "exact_slice",
    status: "collected",
    raw_bytes: 8,
    fields: [
      {
        request_id: label,
        kind: "scalar",
        dtype: "string",
        unit: null,
        unit_truncated: false,
        shape: [],
        total_count: 1,
        start: 0,
        returned_count: 1,
        next_start: null,
        complete: true,
        values: [label],
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
  });

it("does not display a late result after changing identity", async () => {
  const { waitFor, act } = await import("@testing-library/react");
  let release!: (response: Response) => void;
  const fetch = vi.fn(async (url: URL) =>
    url.pathname.includes("late-old")
      ? new Promise<Response>((done) => {
          release = done;
        })
      : response("new-value"),
  );
  vi.stubGlobal("fetch", fetch);
  const view = render(<BoundedMeasurement source={{ recordId: "late-old" }} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  view.rerender(<BoundedMeasurement source={{ recordId: "late-new" }} />);
  await act(async () => release(response("old-value")));
  await openNumericTable();
  expect(await screen.findByRole("cell", { name: "new-value" })).toBeVisible();
  expect(screen.queryByRole("cell", { name: "old-value" })).not.toBeInTheDocument();
});

it("rejects matching slice lengths with mismatched original axes or contracts", async () => {
  const { alignedPreviewFields } = await import("./BoundedMeasurement");
  const field = {
    request_id: "y",
    kind: "array" as const,
    dtype: "float64",
    unit: "V",
    unit_truncated: false,
    shape: [100],
    total_count: 100,
    start: 0,
    returned_count: 2,
    next_start: 2,
    complete: false,
    values: [1, 2],
    truncated_indices: [],
    metadata: {},
    metadata_truncated: false,
  };
  const axis = { ...field, request_id: "x", unit: "Hz" };
  const result = {
    id: "y",
    role: "observable" as const,
    dtype: "float64" as const,
    unit: "V",
    axes: [{ id: "frequency", size: 100, kind: "frequency", coordinate_result: "x", unit: "Hz" }],
  };
  const contract = { id: "x", dtype: "float64" as const, unit: "Hz", role: "coordinate" as const };
  expect(alignedPreviewFields(field, axis, result, contract)).toBe(true);
  expect(
    alignedPreviewFields(field, undefined, {
      ...result,
      axes: [{ id: "sample", size: 100, kind: "sample" }],
    }),
  ).toBe(true);
  expect(
    alignedPreviewFields(field, { ...axis, shape: [101], total_count: 101 }, result, contract),
  ).toBe(false);
  expect(alignedPreviewFields(field, { ...axis, unit: "s" }, result, contract)).toBe(false);
  expect(alignedPreviewFields(field, { ...axis, dtype: "complex128" }, result, contract)).toBe(
    false,
  );
});

it("releases the preview queue after a timed out request", async () => {
  const { waitFor } = await import("@testing-library/react");
  const timeout = new AbortController();
  const timer = vi.spyOn(AbortSignal, "timeout").mockReturnValueOnce(timeout.signal);
  const fetch = vi.fn(async (url: URL, init: RequestInit) => {
    if (url.pathname.includes("/stall/"))
      return new Promise<Response>((_resolve, reject) => {
        init.signal!.addEventListener("abort", () => reject(new Error("preview timeout")), {
          once: true,
        });
      });
    return Response.json({
      method: "exact_slice",
      fields: [],
      status: "collected",
      raw_bytes: 0,
      fields_truncated: false,
      metadata: {},
      metadata_truncated: false,
      problems: [],
      problems_truncated: false,
    });
  });
  vi.stubGlobal("fetch", fetch);
  const first = readMeasurementPreview(
    { recordId: "stall" },
    0,
    new AbortController().signal,
  ).catch((error: Error) => error);
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const second = readMeasurementPreview(
    { recordId: "after-stall" },
    0,
    new AbortController().signal,
  );
  timeout.abort();
  expect(await first).toHaveProperty("message", "preview timeout");
  await expect(second).resolves.toMatchObject({ method: "exact_slice" });
  timer.mockRestore();
});

it("uses narrow-chart collision bounds and compact tick labels without changing scientific coordinates", async () => {
  const fields = [
    { request_id: "frequency", unit: "Hz", values: [4_800_000_000, 4_900_000_000] },
    {
      request_id: "signal",
      unit: "V",
      values: [
        { real: 1, imag: 0 },
        { real: 0, imag: 2 },
      ],
    },
  ].map((field) => ({
    ...field,
    kind: "array",
    dtype: field.request_id === "signal" ? "complex128" : "float64",
    unit_truncated: false,
    shape: [2],
    total_count: 2,
    start: 0,
    returned_count: 2,
    next_start: null,
    complete: true,
    truncated_indices: [],
    metadata: {},
    metadata_truncated: false,
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        method: "exact_slice",
        status: "collected",
        raw_bytes: 32,
        fields,
        fields_truncated: false,
        metadata: {},
        metadata_truncated: false,
        problems: [],
        problems_truncated: false,
      }),
    ),
  );
  render(
    <BoundedMeasurement
      source={{ recordId: "narrow-axis" }}
      acquisition={{
        id: "capture",
        results: [
          { id: "frequency", role: "coordinate", dtype: "float64", unit: "Hz" },
          {
            id: "signal",
            label: "Very long signal label with complete units",
            role: "observable",
            dtype: "complex128",
            unit: "V",
            axes: [
              {
                id: "frequency",
                kind: "frequency",
                coordinate_result: "frequency",
                size: 2,
                unit: "Hz",
              },
            ],
          },
        ],
      }}
    />,
  );
  await screen.findByTestId("bounded-chart");
  const option = vi.mocked(EChart).mock.calls.at(-1)![0].option;
  expect(option).toMatchObject({
    grid: { outerBoundsMode: "same", outerBoundsContain: "all", bottom: 55 },
    xAxis: { name: "frequency (Hz)", splitNumber: 3, axisLabel: { hideOverlap: true, margin: 12 } },
    yAxis: { axisLabel: { hideOverlap: true } },
    series: [
      {
        data: [
          [4_800_000_000, 1],
          [4_900_000_000, 2],
        ],
      },
    ],
  });
  expect(screen.getByText(/^纵轴 /)).toHaveTextContent(
    "纵轴 Very long signal label with complete units (V)；横轴 frequency (Hz)",
  );
  expect(option).toMatchObject({
    xAxis: { axisLabel: { formatter: formatPreviewTick } },
    yAxis: { axisLabel: { formatter: formatPreviewTick } },
    media: [
      {
        query: { maxWidth: 420 },
        option: { xAxis: { name: "" }, yAxis: { name: "" } },
      },
      { option: { xAxis: { name: "frequency (Hz)" }, yAxis: { name: "V" } } },
    ],
  });
  for (const [representation, values, unit] of [
    ["phase", [0, Math.PI / 2], "rad"],
    ["real", [1, 0], "V"],
    ["imag", [0, 2], "V"],
    ["magnitude", [1, 2], "V"],
  ] as const) {
    fireEvent.change(screen.getByLabelText("复数表示"), { target: { value: representation } });
    const current = vi.mocked(EChart).mock.calls.at(-1)![0].option;
    expect(current).toMatchObject({
      tooltip: { trigger: "axis" },
      series: [
        {
          data: [
            [4_800_000_000, values[0]],
            [4_900_000_000, values[1]],
          ],
        },
      ],
      media: [
        { option: { xAxis: { name: "" }, yAxis: { name: "" } } },
        { option: { xAxis: { name: "frequency (Hz)" }, yAxis: { name: unit } } },
      ],
    });
    expect(screen.getByText(/^纵轴 /)).toHaveTextContent(`(${unit})；横轴 frequency (Hz)`);
  }
  expect(new Set([5e9, 5.0002e9, 5.001e9].map(formatPreviewTick)).size).toBe(3);
  expect(formatPreviewTick(4_800_000_000)).toBe("4.8e+9");
  expect(formatPreviewTick(4_800_000_001)).not.toBe(formatPreviewTick(4_800_000_000));
});
