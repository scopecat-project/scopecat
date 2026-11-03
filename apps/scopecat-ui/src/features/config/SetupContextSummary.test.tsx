// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SetupContextSummary } from "./SetupContextSummary";
import { RunSnapshotDetails } from "../runs/RunSnapshotDetails";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const ref = (id: string) => ({ revision_id: id, content_hash: `sha256:${id}` });
function setup(id: string, routed = true) {
  return {
    id,
    content_hash: ref(id).content_hash,
    resolution: {
      definition_id: `bench-${id}`,
      devices: [{ device_id: `device-${id}`, revision_id: `connection-${id}` }],
    },
    setup: {
      instrument_registry: {
        instruments: [{ id: `instrument-${id}`, driver_id: "virtual-sweep" }],
      },
      routing: {
        roles: [{ id: "readout", description: "读出扫描" }],
        routes: routed
          ? [
              {
                id: `route-${id}`,
                instrument_id: `instrument-${id}`,
                role_id: "readout",
                entity_ids: [`object-${id}`],
                endpoints: [
                  { interface_id: "network_sweep/v1", component_path: ["head"], channel_id: "1" },
                ],
              },
            ]
          : [],
      },
      domain_target: null,
    },
  };
}
function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}
function View({ current }: { current: string }) {
  return (
    <>
      <section aria-label="current">
        <SetupContextSummary revision={ref(current)} />
      </section>
      <section aria-label="history">
        <RunSnapshotDetails runId="old-run" />
      </section>
    </>
  );
}

it("keeps the old sample and route snapshot when the current setup changes", async () => {
  const calls: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push(request);
      const path = new URL(request.url).pathname;
      if (path === "/api/v1/runs/old-run")
        return Response.json({
          snapshot: {
            execution_setup: ref("a"),
            config_source: { kind: "config_registry", entry_id: "old-config" },
            scientific_binding: {
              subject: {
                kind: "inline_samples",
                samples: [
                  { sample_id: "chip-a", display_name: "Old chip A", role: "subject", revision: 1 },
                ],
              },
            },
          },
          control: {
            admission: {
              plan: {
                experiment_id: "sweep",
                point_count: 1,
                coordinates: [],
                adaptive_coordinate_ids: [],
              },
            },
          },
        });
      return Response.json(setup(path.split("/").at(-1)!));
    }),
  );
  const view = render(<View current="a" />, { wrapper: wrapper() });
  await screen.findByText("instrument-a");
  const details = screen.getByText("Frozen execution inputs").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  const history = within(screen.getByRole("region", { name: "history" }));
  await history.findByText("instrument-a");
  view.rerender(<View current="b" />);
  const current = within(screen.getByRole("region", { name: "current" }));
  await current.findByText("instrument-b");
  expect(history.getByText(/Old chip A/)).toBeVisible();
  expect(history.getByText("instrument-a")).toBeVisible();
  expect(history.getByText(/路由 route-a/)).toBeVisible();
  expect(history.queryByText("instrument-b")).toBeNull();
  expect(current.getByText(/路由 route-b/)).toBeVisible();
  expect(calls.every((request) => request.method === "GET")).toBe(true);
  expect(calls.map((request) => new URL(request.url).pathname)).toEqual([
    "/api/v1/setup/revisions/a",
    "/api/v1/runs/old-run",
    "/api/v1/setup/revisions/b",
  ]);
});
it("shows absent routes without inventing assignments or connecting a device", async () => {
  const fetch = vi.fn(async () => Response.json(setup("empty", false)));
  vi.stubGlobal("fetch", fetch);
  render(<SetupContextSummary revision={ref("empty")} />, { wrapper: wrapper() });
  expect(await screen.findByText("未分配通用路由")).toBeVisible();
  expect(screen.getByText(/设备登记和独立调试可先进行/)).toBeVisible();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("does not display a different revision returned by the server", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(setup("other"))),
  );
  render(<SetupContextSummary revision={ref("requested")} />, { wrapper: wrapper() });
  expect(await screen.findByRole("alert")).toHaveTextContent("配置摘要与所选版本不符");
  expect(screen.queryByText("instrument-other")).toBeNull();
});
