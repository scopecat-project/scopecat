// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ClearData } from "./DataCleanup";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const selection = {
  runs: ["scan"],
  procedures: [],
  analyses: [],
  setups: [],
  setup_definitions: [],
  parameters: [],
};
function show() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ClearData runs={["scan"]} />
    </QueryClientProvider>,
  );
}
function response(value: unknown) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
}

it("requires reviewing an explicit expanded selection before clearing retained data", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      response({
        selection,
        fingerprint: "first",
        record_count: 1,
        bytes_to_reclaim: 1024,
        blockers: [{ owner: "analysis:derived", reason: "Retains scan" }],
      }),
    )
    .mockResolvedValueOnce(
      response({
        selection: { ...selection, analyses: ["derived"] },
        fingerprint: "second",
        record_count: 2,
        bytes_to_reclaim: 2048,
        blockers: [],
      }),
    )
    .mockResolvedValueOnce(response({ id: "cleanup", state: "complete", selection }));
  vi.stubGlobal("fetch", fetch);
  show();
  fireEvent.click(screen.getByRole("button", { name: "Review data cleanup…" }));
  expect(await screen.findByText("analysis:derived")).toBeVisible();
  expect(screen.getByRole("button", { name: "Delete selected records and files" })).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Include this retaining record and review again" }),
  );
  expect(await screen.findByText("Analysis: derived")).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Delete selected records and files" }));
  expect(await screen.findByText("Records and files cleared.")).toBeVisible();
  const request = fetch.mock.calls[2]?.[0] as Request;
  expect(await request.json()).toMatchObject({
    preview: { fingerprint: "second", selection: { analyses: ["derived"] } },
  });
});

it("reports unfinished physical cleanup and resumes the same operation", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      response({
        selection,
        fingerprint: "one",
        record_count: 1,
        bytes_to_reclaim: 1024,
        blockers: [],
      }),
    )
    .mockResolvedValueOnce(
      response({ id: "retry-me", state: "records_removed", selection, error: "File in use" }),
    )
    .mockResolvedValueOnce(response({ id: "retry-me", state: "complete", selection }));
  vi.stubGlobal("fetch", fetch);
  show();
  fireEvent.click(screen.getByRole("button", { name: "Review data cleanup…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete selected records and files" }));
  expect(await screen.findByText("Records removed; file cleanup needs retry.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Retry cleanup" }));
  expect(await screen.findByText("Records and files cleared.")).toBeVisible();
  const request = fetch.mock.calls[2]?.[0] as Request;
  expect(request.url).toContain("/data-cleanup/retry-me/resume");
});
