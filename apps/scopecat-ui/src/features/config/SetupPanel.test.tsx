// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ConfigProfileSnapshot } from "../../api-contract";
import { SetupPanel } from "./SetupPanel";
import {
  activateSetup,
  getActiveSetup,
  getSetupRevisions,
  saveSetupFromConfig,
  type SetupRevision,
  type ActiveSetupView,
} from "./setup-api";
vi.mock("./setup-api", () => ({
  activateSetup: vi.fn(),
  getActiveSetup: vi.fn(),
  getSetupRevisions: vi.fn(),
  saveSetupFromConfig: vi.fn(),
}));
const revision: SetupRevision = {
  id: "setup-A",
  content_hash: "sha256:setup-a",
  actor: "operator",
  note: "",
  recorded_at: "2026-09-19T00:00:00Z",
  setup: {
    primary_entity_id: "q0",
    topology: { entities: [] },
    instrument_registry: { instruments: [] },
    routing: { roles: [], routes: [] },
    domain_target: null,
  },
};
const next = { ...revision, id: "setup-B", content_hash: "sha256:setup-b" };
const active: ActiveSetupView = {
  revision,
  activation: {
    generation: 3,
    revision: { revision_id: revision.id, content_hash: revision.content_hash },
    previous_revision: null,
    actor: "operator",
    note: "",
    recorded_at: revision.recorded_at,
  },
};
const config: ConfigProfileSnapshot = {
  id: "parameters-A",
  system: {
    id: "system",
    ...revision.setup,
    parameter_catalog: { id: "parameters", definitions: [] },
  },
  parameter_snapshot: { id: "parameters", values: [] },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getActiveSetup).mockResolvedValue(active);
  vi.mocked(getSetupRevisions).mockResolvedValue({ items: [revision, next] });
});
afterEach(cleanup);
function renderPanel() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <SetupPanel config={config} operator="operator" />
    </QueryClientProvider>,
  );
  return cache;
}
it("saves setup from the selected configuration without activating it", async () => {
  vi.mocked(saveSetupFromConfig).mockResolvedValue(next);
  renderPanel();
  await screen.findByRole("option", { name: "setup-B" });
  fireEvent.change(screen.getByLabelText("Setup revision name"), { target: { value: "setup-B" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup from selected configuration" }));
  await waitFor(() =>
    expect(saveSetupFromConfig).toHaveBeenCalledWith(config, "setup-B", "operator"),
  );
  expect(activateSetup).not.toHaveBeenCalled();
});
it("keeps the reviewed generation through refresh and retries exact failed selection", async () => {
  vi.mocked(activateSetup).mockRejectedValue(new Error("selection conflicts with active owner"));
  const cache = renderPanel();
  await screen.findByRole("option", { name: "setup-B" });
  fireEvent.change(screen.getByLabelText("Saved setup"), { target: { value: "setup-B" } });
  fireEvent.click(screen.getByRole("button", { name: "Review setup selection" }));
  expect(activateSetup).not.toHaveBeenCalled();
  act(() => {
    cache.setQueryData(["setup", "active"], {
      ...active,
      activation: { ...active.activation, generation: 4 },
    });
  });
  fireEvent.click(screen.getByRole("button", { name: "Confirm setup selection" }));
  await screen.findByRole("alert");
  expect(activateSetup).toHaveBeenCalledWith(
    expect.objectContaining({
      revision: { revision_id: "setup-B", content_hash: "sha256:setup-b" },
      expected_generation: 3,
      actor: "operator",
    }),
    expect.anything(),
  );
  const first = vi.mocked(activateSetup).mock.calls[0]?.[0];
  fireEvent.click(screen.getByRole("button", { name: "Confirm setup selection" }));
  await waitFor(() => expect(activateSetup).toHaveBeenCalledTimes(2));
  expect(vi.mocked(activateSetup).mock.calls[1]?.[0]).toEqual(first);
});
