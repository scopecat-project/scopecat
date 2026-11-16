// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DirectMeasurementHistory } from "./DirectMeasurementHistory";
import { navigate } from "../../lib/navigation";
import type { SavedMeasurement } from "../instruments/direct-measurement-api";

vi.mock("../instruments/DirectMeasurements", () => ({
  SavedCurve: ({ record }: { record: SavedMeasurement }) => (
    <div data-testid="saved-result">
      {record.id} {record.receipt_digest}
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

function measurement(id: string, title?: string): SavedMeasurement {
  return {
    id,
    saved_at: "2026-11-01T01:00:00Z",
    receipt_validation: "verified" as const,
    collected_at: "2026-11-01T00:59:00Z",
    selection: {
      session_id: "closed-session",
      instrument_id: "vna",
      command_id: id,
      line_label: "Legacy line label",
      note: "",
      title: title ?? "",
      line_id: "",
      series_id: "",
    },
    setup: { revision_id: "setup-v1", content_hash: "setup-hash" },
    description: {
      instrument_id: "vna",
      label: "Bench VNA",
      implementation_id: "virtual.vna",
      implementation_version: "1",
      interfaces: [],
    },
    intent: {
      retain_result: true,
      instrument_id: "vna",
      command_id: id,
      interface_id: "network",
      acquisition_id: "sweep",
    },
    observed_before_collection: null,
    receipt_digest: `digest-${id}`,
  };
}
function show(unavailable = false) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DirectMeasurementHistory projectId="data-space" daemonUnavailable={unavailable} />
    </QueryClientProvider>,
  );
}

it("opens a saved result by stable URL without a live control session or list-page membership", async () => {
  const requests: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      requests.push(url);
      if (url.pathname === "/api/v1/direct-measurements") return Response.json({ items: [] });
      if (url.pathname === "/api/v1/direct-measurements/first")
        return Response.json(measurement("first", "First sweep"));
      throw new Error(`Unexpected request ${url.pathname}`);
    }),
  );
  window.history.replaceState({}, "", "/?data=direct-measurements&measurement=first#history");
  const view = show();
  expect(await screen.findByTestId("saved-result")).toHaveTextContent("first digest-first");
  expect(requests.every((url) => !url.pathname.includes("instrument"))).toBe(true);
  view.unmount();
  show();
  expect(await screen.findByTestId("saved-result")).toHaveTextContent("first digest-first");
});

it("lists new titles and legacy labels, paginates, and keeps the selected result while filtering", async () => {
  const first = measurement("first", "First sweep");
  const older = measurement("older");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/direct-measurements") {
        return Response.json(
          url.searchParams.has("before") ? { items: [older] } : { items: [first], next_cursor: 10 },
        );
      }
      if (url.pathname.endsWith("/first")) return Response.json(first);
      if (url.pathname.endsWith("/older")) return Response.json(older);
      throw new Error(`Unexpected request ${url.pathname}`);
    }),
  );
  show();
  fireEvent.click(await screen.findByRole("button", { name: /First sweep/ }));
  expect(new URL(window.location.href).searchParams.get("measurement")).toBe("first");
  expect(await screen.findByTestId("saved-result")).toHaveTextContent("digest-first");
  fireEvent.click(screen.getByRole("button", { name: "加载更早记录" }));
  expect(await screen.findByRole("button", { name: /Legacy line label/ })).toBeVisible();
  fireEvent.change(screen.getByLabelText("搜索已加载记录"), { target: { value: "older" } });
  expect(screen.queryByRole("button", { name: /First sweep/ })).not.toBeInTheDocument();
  expect(screen.getByTestId("saved-result")).toHaveTextContent("digest-first");
  fireEvent.click(screen.getByRole("button", { name: /Legacy line label/ }));
  await waitFor(() => expect(screen.getByTestId("saved-result")).toHaveTextContent("digest-older"));
  act(() => navigate("/?data=direct-measurements&measurement=first#history"));
  await waitFor(() => expect(screen.getByTestId("saved-result")).toHaveTextContent("digest-first"));
});

it("does not issue data requests while the daemon is unavailable", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  window.history.replaceState({}, "", "/?data=direct-measurements&measurement=first#history");
  show(true);
  expect(screen.getByRole("status")).toHaveTextContent("应用服务离线");
  expect(fetch).not.toHaveBeenCalled();
});

it("reports a missing selected record without hiding the independent saved list", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      return url.pathname === "/api/v1/direct-measurements"
        ? Response.json({ items: [measurement("other", "Another measurement")] })
        : Response.json({ detail: "saved direct measurement not found" }, { status: 404 });
    }),
  );
  window.history.replaceState({}, "", "/?data=direct-measurements&measurement=deleted#history");
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent("打开测量失败");
  expect(screen.getByRole("button", { name: /Another measurement/ })).toBeVisible();
});
