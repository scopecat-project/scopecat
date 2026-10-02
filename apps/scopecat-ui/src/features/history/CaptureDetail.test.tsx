// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CaptureDetail } from "./CaptureDetail";

vi.mock("../../ui/EChartRuntime", () => ({
  EChartRuntime: ({ option }: { option: { series: { data: number[][] }[] } }) => (
    <output data-testid="chart-values">
      {JSON.stringify(option.series.map((series) => series.data))}
    </output>
  ),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("keeps captures with the same run ID separate and requests retained selection explicitly", async () => {
  const requests: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      requests.push(url);
      const source = url.pathname.includes("/first/") ? "first" : "second";
      if (url.pathname.endsWith("/evidence"))
        return Response.json({
          source_project_id: source,
          roots: ["scan"],
          runs: ["scan", "reference"].map((runId) => ({
            snapshot: { run_id: runId },
            request: { experiment_id: `${source} ${runId}` },
            configuration: {},
          })),
        });
      const selected = url.searchParams.get("selection") === "selected";
      const offset = Number(url.searchParams.get("offset"));
      return Response.json({
        selection: selected ? "selected" : "acquired",
        record_count: 101,
        selected_record_count: 1,
        offset,
        next_offset: offset === 0 ? 100 : null,
        dataset_schema: {
          dimensions: [{ id: "point", kind: "point", size: 1 }],
          variables: [
            { id: "signal", role: "observable", dtype: "float64", dims: ["point"], unit: "V" },
          ],
          point_domain: { kind: "point_cloud", columns: [] },
        },
        items: [
          {
            point_index: 0,
            coordinates: {},
            observables: {
              signal: {
                kind: "scalar",
                dtype: "float64",
                unit: "V",
                value: offset + (source === "first" ? (selected ? 12 : 11) : 22),
              },
            },
          },
        ],
      });
    }),
  );
  const mounted = render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <div data-testid="first">
        <CaptureDetail contentHash="first" />
      </div>
      <div data-testid="second">
        <CaptureDetail contentHash="second" />
      </div>
    </QueryClientProvider>,
  );
  const first = within(screen.getByTestId("first"));
  const second = within(screen.getByTestId("second"));
  await first.findByText("11 V");
  await second.findByText("22 V");
  expect((await first.findByTestId("chart-values")).textContent).toBe("[[[0,11]]]");
  expect(first.getByText("Charts show only this page (1 record).")).toBeTruthy();
  fireEvent.change(first.getByLabelText("Measurements"), { target: { value: "selected" } });
  await first.findByText("12 V");
  expect(first.getByTestId("chart-values").textContent).toBe("[[[0,12]]]");
  expect(second.getByText("22 V")).toBeTruthy();
  expect(
    requests.some(
      (url) =>
        url.pathname.includes("/second/") && url.searchParams.get("selection") === "selected",
    ),
  ).toBe(false);
  fireEvent.change(first.getByLabelText("Run"), { target: { value: "reference" } });
  await first.findByText("11 V");
  expect(first.getByRole("heading", { name: "first reference" })).toBeTruthy();
  fireEvent.click(first.getByRole("button", { name: "Next measurements" }));
  await first.findByText("111 V");
  expect(first.getByTestId("chart-values").textContent).toBe("[[[0,111]]]");
  fireEvent.change(first.getByLabelText("Measurements"), { target: { value: "selected" } });
  await first.findByText("12 V");
  act(() => window.history.back());
  await first.findByText("111 V");
  await waitFor(() =>
    expect(first.getByLabelText("Measurements")).toHaveProperty("value", "acquired"),
  );
  expect(second.getByText("22 V")).toBeTruthy();

  // Leaving and recreating the view restores the same source, run and page.
  mounted.unmount();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CaptureDetail contentHash="first" />
    </QueryClientProvider>,
  );
  await screen.findByText("111 V");
  expect(screen.getByRole("heading", { name: "first reference" })).toBeTruthy();
});
