// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { scenarioFixture } from "../../test/scenario-fixture";
import { ConfigurationTemplatesPanel } from "./ConfigurationTemplatesPanel";
import {
  getConfigurationTemplates,
  importConfigurationTemplate,
  type ConfigurationTemplateImportResult,
} from "./setup-api";
vi.mock("./setup-api", () => ({
  getConfigurationTemplates: vi.fn(),
  importConfigurationTemplate: vi.fn(),
}));
const config = {
  id: "reference",
  system: {
    id: "system",
    primary_entity_id: "q0",
    topology: { entities: [] },
    instrument_registry: { instruments: [] },
    routing: { roles: [], routes: [] },
    domain_target: null,
    parameter_catalog: { id: "parameters", definitions: [] },
    scenario: scenarioFixture,
  },
  parameter_snapshot: { id: "parameters", values: [] },
};
const result: ConfigurationTemplateImportResult = {
  setup: {
    id: "imported-setup",
    content_hash: "sha256:setup",
    actor: "operator",
    note: "",
    recorded_at: "2026-09-21T00:00:00Z",
    setup: { ...config.system },
  },
  parameters: {
    id: "imported-config",
    content_hash: "sha256:config",
    recorded_at: "2026-09-21T00:00:00Z",
    actor: "operator",
    note: "",
    catalog: config.system.parameter_catalog,
    parameters: config.parameter_snapshot,
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getConfigurationTemplates).mockResolvedValue({
    items: [
      {
        id: "resonance",
        label: "Resonance template",
        description: "Prepared software lab",
        content_hash: "sha256:template",
        setup: result.setup.setup,
        catalog: result.parameters.catalog,
        parameters: result.parameters.parameters,
      },
    ],
  });
});
afterEach(cleanup);
it("imports exact template evidence, retries the same entry, and waits for explicit setup activation", async () => {
  vi.mocked(importConfigurationTemplate)
    .mockRejectedValueOnce(new Error("connection interrupted"))
    .mockResolvedValue(result);
  const onImported = vi.fn(async () => {});
  const onSelectConfiguration = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = (activeSetupHash: string) => (
    <QueryClientProvider client={cache}>
      <ConfigurationTemplatesPanel
        actor="operator"
        activeSetupHash={activeSetupHash}
        onImported={onImported}
        onSelectConfiguration={onSelectConfiguration}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(view("sha256:original"));
  await screen.findByRole("option", { name: "Resonance template" });
  fireEvent.change(screen.getByLabelText("Available template"), {
    target: { value: "resonance" },
  });
  await screen.findByText("Resonance frequency sweep");
  fireEvent.click(screen.getByRole("button", { name: "Import configuration template" }));
  await screen.findByRole("alert");
  const command = vi.mocked(importConfigurationTemplate).mock.calls[0]![0];
  expect(command).toMatchObject({
    template_id: "resonance",
    content_hash: "sha256:template",
    actor: "operator",
  });
  expect(command.revision_id).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry template import" }));
  await screen.findByText("imported-config");
  expect(vi.mocked(importConfigurationTemplate).mock.calls[1]![0]).toEqual(command);
  expect(onImported).toHaveBeenCalledWith(result);
  const use = screen.getByRole("button", {
    name: "Use imported configuration for next experiment",
  });
  expect(use).toBeDisabled();
  expect(onSelectConfiguration).not.toHaveBeenCalled();
  rerender(view("sha256:setup"));
  fireEvent.click(use);
  await waitFor(() =>
    expect(onSelectConfiguration).toHaveBeenCalledWith({
      kind: "parameters",
      ref: { revision_id: "imported-config", content_hash: "sha256:config" },
      setup: { revision_id: "imported-setup", content_hash: "sha256:setup" },
    }),
  );
});
