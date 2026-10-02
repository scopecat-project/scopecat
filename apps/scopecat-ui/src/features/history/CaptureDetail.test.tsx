// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CaptureDetail } from "./CaptureDetail";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
          runs: [
            {
              snapshot: { run_id: "scan" },
              request: { experiment_id: `${source} experiment` },
              configuration: {},
            },
          ],
        });
      const selected = url.searchParams.get("selection") === "selected";
      return Response.json({
        selection: selected ? "selected" : "acquired",
        record_count: 1,
        selected_record_count: 1,
        offset: 0,
        next_offset: null,
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
                value: source === "first" ? (selected ? 12 : 11) : 22,
              },
            },
          },
        ],
      });
    }),
  );
  render(
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
  fireEvent.change(first.getByLabelText("Measurements"), { target: { value: "selected" } });
  await first.findByText("12 V");
  expect(second.getByText("22 V")).toBeTruthy();
  expect(
    requests.some(
      (url) =>
        url.pathname.includes("/second/") && url.searchParams.get("selection") === "selected",
    ),
  ).toBe(false);
});
