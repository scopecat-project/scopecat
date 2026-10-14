// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import type { SampleView } from "./api-contract";
import { getEvents, getHealth } from "./data/project-api";
import {
  getSample,
  getSampleAnalyses,
  getSampleRevisions,
  getSampleRuns,
  getSamples,
} from "./features/samples/sample-api";

vi.mock("./data/project-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./data/project-api")>()),
  getEvents: vi.fn(),
  getHealth: vi.fn(),
}));
vi.mock("./features/samples/sample-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./features/samples/sample-api")>()),
  getSample: vi.fn(),
  getSampleAnalyses: vi.fn(),
  getSampleRevisions: vi.fn(),
  getSampleRuns: vi.fn(),
  getSamples: vi.fn(),
}));

const healthy = {
  status: "ok",
  projectId: "local:test",
  projectName: "Test lab",
  projectRoot: "/tmp/test-lab",
  details: {},
};
const unavailable = new Error("The daemon returned 502 Bad Gateway");
let client: QueryClient;
let sseOpen: (() => void) | undefined;

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  window.history.replaceState(null, "", "/?sample=chip-a17#samples");
  vi.stubGlobal("scrollTo", vi.fn());
  sseOpen = undefined;
  vi.stubGlobal(
    "EventSource",
    class {
      addEventListener(type: string, listener: () => void) {
        if (type === "open") sseOpen = listener;
      }
      removeEventListener() {}
      close() {}
    },
  );
  vi.mocked(getHealth).mockResolvedValue(healthy);
  vi.mocked(getEvents).mockResolvedValue([]);
  vi.mocked(getSamples).mockResolvedValue({ items: [sampleView()] });
  vi.mocked(getSample).mockResolvedValue(sampleView());
  vi.mocked(getSampleRuns).mockResolvedValue({ items: [] });
  vi.mocked(getSampleRevisions).mockResolvedValue({
    sample_id: "chip-a17",
    items: [sampleView().revision],
  });
  vi.mocked(getSampleAnalyses).mockResolvedValue({ sample_id: "chip-a17", items: [] });
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.unstubAllGlobals();
});

function renderApp() {
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}
async function checkHealth() {
  await act(async () => {
    await client.refetchQueries({ queryKey: ["health"] });
  });
}
async function expectRecovered() {
  expect(await screen.findByRole("heading", { name: "Chip A17" })).toBeVisible();
  await waitFor(() => expect(screen.queryByText("Registry unavailable")).not.toBeInTheDocument());
  const metrics = screen.getByRole("region", { name: "Sample registry status" });
  expect(within(metrics).getAllByText("1")).toHaveLength(3);
  expect(window.location.search).toBe("?sample=chip-a17");
  expect(window.location.hash).toBe("#samples");
}

it("recovers the mounted registry and selection after health recovers without SSE open", async () => {
  renderApp();
  await expectRecovered();
  vi.mocked(getHealth).mockRejectedValue(unavailable);
  vi.mocked(getSamples).mockRejectedValue(unavailable);
  await checkHealth();
  await act(async () => {
    await client.refetchQueries({ queryKey: ["samples"] });
  });
  expect(await screen.findByText("Registry unavailable")).toBeVisible();
  vi.mocked(getHealth).mockResolvedValue(healthy);
  vi.mocked(getSamples).mockResolvedValue({ items: [sampleView()] });
  await checkHealth();
  await expectRecovered();
  const count = vi.mocked(getSamples).mock.calls.length;
  await checkHealth();
  expect(getSamples).toHaveBeenCalledTimes(count);
  // A delayed stream connection still uses the existing refresh path.
  await act(async () => {
    sseOpen?.();
  });
  await waitFor(() => expect(getSamples).toHaveBeenCalledTimes(count + 1));
  await expectRecovered();
});

it("replaces an unfinished first registry request and ignores its late 502", async () => {
  let rejectOld!: (error: Error) => void;
  let oldSignal: AbortSignal | undefined;
  vi.mocked(getHealth).mockRejectedValue(unavailable);
  vi.mocked(getSamples).mockImplementationOnce((_cursor, signal) => {
    oldSignal = signal;
    return new Promise((_resolve, reject) => {
      rejectOld = reject;
    });
  });
  renderApp();
  await waitFor(() => expect(getSamples).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(client.getQueryState(["health"])?.status).toBe("error"));
  vi.mocked(getHealth).mockResolvedValue(healthy);
  await checkHealth();
  await waitFor(() => expect(getSamples).toHaveBeenCalledTimes(2));
  expect(oldSignal?.aborted).toBe(true);
  await expectRecovered();
  await act(async () => {
    rejectOld(unavailable);
  });
  await expectRecovered();
  expect(client.getQueryState(["samples"])?.status).toBe("success");
});

function sampleView(): SampleView {
  const revision = {
    sample_id: "chip-a17",
    revision: 2,
    content_hash: `sha256:${"a".repeat(64)}`,
    recorded_at: "2026-08-25T08:00:00Z",
    actor: "operator",
    note: "Mounted in fridge 2",
    content: {
      display_name: "Chip A17",
      status: "mounted" as const,
      design_ref: "mask:g4-r3",
      aliases: ["A17"],
      tags: ["fridge-2", "generation-4"],
      relations: [{ kind: "diced-from", sample_id: "wafer-4" }],
      properties: { cooldown: 3 },
      topology: {
        entities: [
          { id: "q0", kind: "qubit", metadata: {} },
          { id: "q1", kind: "qubit", metadata: {} },
        ],
        connections: [
          {
            id: "q0-q1",
            kind: "coupling",
            endpoints: ["q0", "q1"],
          },
        ],
      },
      geometry: {
        kind: "cartesian" as const,
        unit: "mm",
        width: 10,
        height: 10,
        points: [
          { entity_id: "q0", x: 3, y: 5 },
          { entity_id: "q1", x: 7, y: 5 },
        ],
      },
      artifacts: [],
    },
  };
  return {
    record: {
      id: "chip-a17",
      kind: "chip",
      active_revision: 2,
      created_at: "2026-08-20T08:00:00Z",
    },
    revision,
    run_count: 1,
    last_run_at: "2026-08-25T10:00:00Z",
  };
}
