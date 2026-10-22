// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { RunSnapshotDetails } from "./RunSnapshotDetails";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("loads only the selected run on demand and keeps its frozen identity", async () => {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(new URL(request.url).pathname);
      return Response.json({
        snapshot: {
          config_content_hash: "frozen-a",
          config_source: { kind: "config_registry", entry_id: "config-a" },
          scientific_binding: { subject: { kind: "unbound" }, setup_content_hash: "setup-a" },
        },
        control: {
          admission: {
            plan: {
              experiment_id: "experiment-a",
              point_count: 3,
              coordinates: [
                {
                  id: "frequency",
                  unit: "Hz",
                  sampled_values: [
                    { value: 10, unit: "Hz" },
                    { value: 20, unit: "Hz" },
                  ],
                  sampled_values_truncated: true,
                },
              ],
              adaptive_coordinate_ids: [],
            },
          },
        },
      });
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RunSnapshotDetails runId="run-a" />
    </QueryClientProvider>,
  );
  expect(requests).toEqual([]);
  const details = screen.getByText("Frozen execution inputs").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await screen.findByText("frozen-a");
  expect(requests).toEqual(["/api/v1/runs/run-a"]);
  expect(screen.getByText("Saved configuration · config-a")).toBeInTheDocument();
  expect(screen.getByText("3 planned points")).toBeInTheDocument();
  expect(screen.getByText(/10 Hz, 20 Hz … \(sample only\)/)).toBeInTheDocument();
  expect(document.querySelector("pre")).toBeNull();
  details.open = false;
  fireEvent(details, new Event("toggle"));
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("requires an editor experiment for configuration reuse and never offers full recipe replay", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        snapshot: {
          config_content_hash: "frozen-a",
          config_source: {
            kind: "parameter_revision",
            parameters: { revision_id: "parameters-a", content_hash: "parameters-hash" },
            setup: { revision_id: "setup-a", content_hash: "setup-hash" },
            content_hash: "frozen-a",
            overrides: [],
          },
          scientific_binding: { subject: { kind: "unbound" }, setup_content_hash: "setup-a" },
        },
        control: {
          admission: {
            plan: {
              experiment_id: "original-experiment",
              point_count: 1,
              coordinates: [],
              adaptive_coordinate_ids: [],
            },
          },
        },
      }),
    ),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RunSnapshotDetails runId="run-a" />
    </QueryClientProvider>,
  );
  const details = screen.getByText("Frozen execution inputs").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(
    await screen.findByRole("button", { name: "Use this configuration in a new draft" }),
  ).toBeDisabled();
  expect(screen.getByText("Choose an experiment in the editor first.")).toBeVisible();
  expect(screen.getByText(/does not recreate the original run's recipe/)).toBeVisible();
});
