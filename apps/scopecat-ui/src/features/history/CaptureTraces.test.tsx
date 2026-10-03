// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { entityTraceSchema, tracePreview } from "../runs/measurement-trace.test-support";
import { CaptureTraces } from "./CaptureTraces";

vi.mock("../../ui/EChartRuntime", () => ({ EChartRuntime: () => null }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("selects acquisition offsets and entities without hiding unavailable traces or errors", async () => {
  const requests: { url: URL; body: { entity_indices?: number[]; value_mode?: string } }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const body = (await request.json()) as { entity_indices?: number[]; value_mode?: string };
      requests.push({ url, body });
      if (body.value_mode === "phase")
        return Response.json({ detail: "Cannot read stored trace" }, { status: 409 });
      return Response.json(
        url.searchParams.get("offset") === "20"
          ? tracePreview()
          : tracePreview({
              series: [],
              failures: [{ point_index: 0, label: "Second acquisition", reasons: ["missing"] }],
              returned_series_count: 0,
              source_sample_count: 0,
              returned_sample_count: 0,
            }),
      );
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <CaptureTraces
        contentHash="source-a"
        runId="scan"
        selection="acquired"
        offset={20}
        records={[{ point_index: 0 }, { point_index: 0 }]}
        schema={entityTraceSchema()}
      />
    </QueryClientProvider>,
  );
  await screen.findByRole("img");
  expect(requests[0]!.url.pathname).toContain("/source-a/runs/scan/recording/traces");
  expect(requests[0]!.url.searchParams.get("selection")).toBe("acquired");
  expect(requests[0]!.url.searchParams.get("limit")).toBe("1");
  fireEvent.change(screen.getByLabelText("Waveform entity"), { target: { value: "1" } });
  await waitFor(() => expect(requests.at(-1)!.body.entity_indices).toEqual([1]));
  fireEvent.change(screen.getByLabelText("Waveform record"), { target: { value: "1" } });
  await screen.findByText("Second acquisition");
  expect(requests.at(-1)!.url.searchParams.get("offset")).toBe("21");
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText("Unavailable · missing")).toBeTruthy();
  const waveform = screen.getByLabelText("Waveform") as HTMLSelectElement;
  const phase = Array.from(waveform.options).find((option) => option.value.endsWith(":phase"))!;
  fireEvent.change(waveform, { target: { value: phase.value } });
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Trace unavailable: Cannot read stored trace",
  );
  expect(screen.queryByText("Second acquisition")).toBeNull();
});
