// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ImportedCaptures } from "./ImportedCaptures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("imports through the native picker and preserves data after cancelled or failed saves", async () => {
  const capture = {
    content_hash: "sha256:" + "a".repeat(64),
    source_project_id: "origin",
    roots: ["scan-A"],
  };
  let imported = false;
  let finishOpen: (() => void) | undefined;
  const open = vi.fn(
    () =>
      new Promise((resolve) => {
        finishOpen = () => {
          imported = true;
          resolve({ capture, created: false });
        };
      }),
  );
  const save = vi
    .fn()
    .mockResolvedValueOnce(null)
    .mockRejectedValueOnce(new Error("Destination is read-only"));
  vi.stubGlobal("pywebview", { api: { open_capture: open, save_capture: save } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (new URL(request.url).pathname.endsWith("/evidence"))
        return Response.json({
          source_project_id: "origin",
          roots: ["scan-A"],
          runs: [
            {
              snapshot: { run_id: "scan-A" },
              request: { experiment_id: "scan" },
              configuration: {},
            },
          ],
        });
      if (new URL(request.url).pathname.endsWith("/recording"))
        return Response.json({
          record_count: 0,
          selected_record_count: 0,
          next_offset: null,
          items: [],
          dataset_schema: {
            dimensions: [],
            variables: [],
            point_domain: { kind: "point_cloud", columns: [] },
          },
        });
      return Response.json(imported ? [capture] : []);
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ImportedCaptures unavailable={false} />
    </QueryClientProvider>,
  );
  await screen.findByText("No imported data yet.");
  fireEvent.click(screen.getByRole("button", { name: "Open Scopecat file…" }));
  await screen.findByText("Opening and checking the selected file…");
  expect(screen.getByRole("button", { name: "Open Scopecat file…" })).toHaveProperty(
    "disabled",
    true,
  );
  finishOpen!();
  await screen.findByText("This data is already available.");
  await screen.findByText("Runs: scan-A");
  fireEvent.click(screen.getByRole("button", { name: "Save a copy…" }));
  await waitFor(() => expect(screen.queryByText("Saving file…")).toBeNull());
  expect(screen.queryByText(/^Saved to/)).toBeNull();
  expect(screen.getByText("Runs: scan-A")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save a copy…" }));
  await screen.findByRole("alert");
  expect(screen.getByText("Destination is read-only")).toBeTruthy();
  expect(save).toHaveBeenLastCalledWith(capture.content_hash);
  expect(screen.getByText("Runs: scan-A")).toBeTruthy();
});
