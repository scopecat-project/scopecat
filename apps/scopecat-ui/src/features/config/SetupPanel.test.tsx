// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { scenarioFixture } from "../../test/scenario-fixture";
import { getDevices } from "../instruments/device-api";
import { SetupPanel } from "./SetupPanel";
import { getConfigurationTemplates, getSetupDefinitions, saveSetupDefinition } from "./setup-api";

vi.mock("./setup-api", () => ({
  getConfigurationTemplates: vi.fn(),
  importConfigurationTemplate: vi.fn(),
  getSetupDefinitions: vi.fn(),
  saveSetupDefinition: vi.fn(),
}));
vi.mock("../instruments/device-api", () => ({ getDevices: vi.fn() }));
const definition = {
  topology: { entities: [] },
  routing: { roles: [], routes: [] },
  instruments: [],
  domain_target: null,
  scenario: scenarioFixture,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getConfigurationTemplates).mockResolvedValue({ items: [] });
  vi.mocked(getDevices).mockResolvedValue({ items: [] });
  vi.mocked(getSetupDefinitions).mockResolvedValue({
    items: [
      {
        id: "bench",
        definition,
        actor: "operator",
        note: "",
        purpose: "experiment",
      },
    ],
  });
});
afterEach(cleanup);
function renderPanel() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <SetupPanel operator="operator" />
    </QueryClientProvider>,
  );
}
it("edits a definition without copying connections or selecting a global setup", async () => {
  vi.mocked(saveSetupDefinition).mockResolvedValue({
    id: "resolved",
    content_hash: "sha256:resolved",
    actor: "operator",
    note: "",
    resolution: {
      definition_id: "bench revised",
      definition_hash: "sha256:definition",
      devices: [],
    },
    setup: {
      topology: {},
      instrument_registry: { instruments: [] },
      routing: {},
      domain_target: null,
    },
  });
  renderPanel();
  await screen.findByRole("option", { name: "bench" });
  fireEvent.change(screen.getByLabelText("Saved setup"), { target: { value: "bench" } });
  fireEvent.change(screen.getByLabelText("Save as"), { target: { value: "bench revised" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup" }));
  await waitFor(() =>
    expect(saveSetupDefinition).toHaveBeenCalledWith(definition, "bench revised", "operator"),
  );
  expect(screen.queryByText("Confirm setup selection")).not.toBeInTheDocument();
});
it("starts an empty definition and preserves the draft when saving fails", async () => {
  vi.mocked(saveSetupDefinition).mockRejectedValue(
    new Error("Device connection changed; review Devices"),
  );
  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "New setup" }));
  expect(screen.getByRole("button", { name: "Save setup" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Save as"), { target: { value: "diagnostic" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Device connection changed");
  expect(screen.getByLabelText("Save as")).toHaveValue("diagnostic");
});
