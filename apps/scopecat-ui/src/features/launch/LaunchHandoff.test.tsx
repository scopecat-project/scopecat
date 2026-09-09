// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchDraftProvider, useLaunchDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";
import type { ComparisonHandoff } from "../analyses/RunComparison";

const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "Signal",
  kind: "diagnostic",
  configuration_effect: "none",
  request: { properties: {}, required: [] },
  actions: ["preview", "submit"],
  controls: [],
};
const suggestion: ComparisonHandoff = {
  kind: "handoff",
  source_run: "source",
  source_analysis: "fit-r1",
  source_hash: "sha256:fit",
  request: {
    action: "preview",
    experiment: "signal",
    version: "1",
    actor: "operator",
    request_key: "",
    overrides: [],
    inputs: {},
  },
};
function Probe() {
  const state = useLaunchDraft();
  return (
    <>
      <button
        onClick={() =>
          void state.submit(
            {
              action: "submit",
              experiment: "original",
              version: "1",
              actor: "operator",
              request_key: "unknown-original",
              overrides: [],
            },
            "original-definition",
          )
        }
      >
        Lose receipt
      </button>
      <button onClick={() => state.importHandoff(entry, suggestion)}>Import</button>
      <output aria-label="Attempt">
        {state.attempt?.status}:{state.attempt?.request.request_key}
      </output>
      <output aria-label="Source">{state.draft?.handoff?.source_hash}</output>
      <output aria-label="Experiment">{state.draft?.experiment}</output>
    </>
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("imports a new suggested draft while retaining an uncertain original submission and project isolation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("Response lost");
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="one">
        <Probe />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("Lose receipt"));
  await screen.findByText("unknown:unknown-original");
  fireEvent.click(screen.getByText("Import"));
  expect(screen.getByLabelText("Source")).toHaveTextContent("sha256:fit");
  expect(screen.getByLabelText("Experiment")).toHaveTextContent("signal");
  expect(screen.getByLabelText("Attempt")).toHaveTextContent("unknown:unknown-original");
  view.rerender(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="two">
        <Probe />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
  expect(screen.getByLabelText("Source")).toBeEmptyDOMElement();
  expect(screen.getByLabelText("Experiment")).toBeEmptyDOMElement();
});
