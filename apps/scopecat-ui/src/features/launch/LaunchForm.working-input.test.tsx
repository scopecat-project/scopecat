// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useEffect, useRef } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchDraftProvider, useLaunchDraft } from "./LaunchDraft";
import { LaunchForm } from "./LaunchForm";
import type { LaunchCatalogEntry } from "./launch-api";
import type { ParameterDraftView } from "../config/parameter-draft-api";
vi.mock("./MeasurementContext", () => ({ MeasurementContext: () => null }));
vi.mock("./PlanSave", () => ({ PlanSave: () => null }));
const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "Synthetic signal",
  actions: ["preview", "submit"],
  controls: [],
  request: { properties: {} },
  kind: "diagnostic",
  configuration_effect: "none",
};
const configuration = {
  kind: "parameters" as const,
  ref: { revision_id: "baseline-a", content_hash: "sha256:base" },
  setup: { revision_id: "bench", content_hash: "sha256:bench" },
  overrides: [],
};
const source: ParameterDraftView = {
  head_revision: 3,
  branch_changed: false,
  draft: {
    draft_id: "table-a",
    revision: 3,
    working_branch: "browser",
    base: configuration.ref,
    state: "saved",
    created_at: "2026-10-06T00:00:00Z",
    input: { name: "", actor: "operator", branch: "browser", note: "", values: [] },
  },
};
function Form() {
  const { draft, select, selectConfiguration } = useLaunchDraft();
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    select(entry);
    selectConfiguration(configuration, { draft_id: "table-a", revision: 3 });
  }, [select, selectConfiguration]);
  return draft ? <LaunchForm entry={entry} catalogReady onAdmitted={() => {}} /> : null;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount(read: () => Promise<Response>) {
  const fetcher = vi.fn(async (request: Request) => {
    if (request.url.endsWith("/freeze"))
      return Response.json({ draft_id: "table-a", revision: 4, configuration });
    return read();
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="test">
        <Form />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
  return { client, fetcher };
}
it("keeps the adopted copy after edits and requires adoption before preview", async () => {
  let latest = source;
  const { client, fetcher } = mount(async () => Response.json(latest));
  await screen.findByText(/adopted copy matches the saved working table/);
  const preview = screen.getByRole("button", { name: "Preview" });
  expect(preview).toBeEnabled();
  expect(
    screen.getByText(/Parameter baseline: revision baseline-a with no overrides/),
  ).toBeVisible();
  latest = { ...source, head_revision: 4, draft: { ...source.draft, revision: 4 } };
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["launch-working-input"] });
  });
  expect(screen.getByText(/Using working input revision 3/)).toBeVisible();
  await screen.findByText(/The working table has newer saved edits/);
  expect(preview).toBeDisabled();
  expect(preview).toHaveAccessibleDescription(/newer saved edits.*Use current working inputs/);
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
  expect(fetcher.mock.calls.some(([request]) => request.url.endsWith("/freeze"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Use current working inputs" }));
  await screen.findByText(/Using working input revision 4/);
  expect(preview).toBeEnabled();
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
  expect(screen.getByText(/Submitted experiments keep the inputs captured/)).toBeVisible();
});
it("distinguishes a pending check from an unavailable source without replacing inputs", async () => {
  let resolve!: (response: Response) => void;
  mount(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  const preview = await screen.findByRole("button", { name: "Preview" });
  expect(preview).toBeDisabled();
  expect(preview).toHaveAccessibleDescription(/Checking the source working table/);
  await act(async () => {
    resolve(Response.json({ detail: "Service unavailable" }, { status: 503 }));
  });
  await screen.findByText(/Cannot check the source working table/);
  expect(preview).toHaveAccessibleDescription(/Cannot check.*adopted copy is unchanged/);
  expect(screen.getByText(/Using working input revision 3/)).toBeVisible();
});
it.each([
  ["branch", { ...source, branch_changed: true }, /Review the latest branch head in Configuration/],
  [
    "closed",
    { ...source, draft: { ...source.draft, state: "completed" } },
    /closed or conflicted.*Open Configuration/,
  ],
])("explains the %s source recovery action", async (_name, view, message) => {
  mount(async () => Response.json(view));
  await screen.findByText(message);
  expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
});
it("does not trust a cached matching source when its next check fails", async () => {
  let unavailable = false;
  const { client } = mount(async () =>
    unavailable
      ? Response.json({ detail: "Service unavailable" }, { status: 503 })
      : Response.json(source),
  );
  await screen.findByText(/adopted copy matches the saved working table/);
  unavailable = true;
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["launch-working-input"] });
  });
  await screen.findByText(/Cannot check the source working table/);
  expect(client.getQueryData(["launch-working-input", "test", "table-a"])).toEqual(source);
  expect(screen.getByText(/Using working input revision 3/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
});
