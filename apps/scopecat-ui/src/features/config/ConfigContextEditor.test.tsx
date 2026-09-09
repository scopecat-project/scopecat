// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ConfigProfileSnapshot, ConfigRegistryEntry } from "../../api-contract";
import { ConfigContextEditor } from "./ConfigContextEditor";
import { getSamples } from "../samples/sample-api";
import { saveConfigContext } from "./config-api";
vi.mock("../samples/sample-api", () => ({ getSamples: vi.fn() }));
vi.mock("./config-api", async (original) => ({
  ...(await original<typeof import("./config-api")>()),
  saveConfigContext: vi.fn(),
}));
afterEach(cleanup);

const config: ConfigProfileSnapshot = {
  id: "lab",
  system: {
    id: "system",
    domain_target: null,
    primary_entity_id: "q0",
    topology: { entities: [] },
    instrument_registry: { instruments: [] },
    routing: { roles: [], routes: [] },
    parameter_catalog: {
      id: "catalog",
      definitions: [
        {
          id: "frequency",
          value_type: { shape: "scalar", atom: { type: "quantity", finite: true, unit: "GHz" } },
        },
        { id: "unknown", value_type: { shape: "scalar", atom: { type: "float", finite: true } } },
      ],
    },
  },
  parameter_snapshot: {
    id: "values",
    values: [{ id: "frequency", shape: "scalar", value: { value: 4.8, unit: "GHz" } }],
  },
};
const entry: ConfigRegistryEntry = {
  id: "base",
  content_hash: `sha256:${"a".repeat(64)}`,
  config_ref: "base.json",
  actor: "maintainer",
  note: "",
  source: { kind: "direct_config_profile" },
};

it("saves an explicit sample/working point copy and leaves untouched unknown values absent", async () => {
  vi.mocked(getSamples).mockResolvedValue({
    items: [
      {
        run_count: 0,
        record: { id: "sample-a", kind: "synthetic", active_revision: 2 },
        revision: {
          sample_id: "sample-a",
          revision: 2,
          content_hash: `sha256:${"b".repeat(64)}`,
          actor: "operator",
          note: "",
          content: {
            display_name: "Sample A",
            aliases: [],
            artifacts: [],
            relations: [],
            status: "available",
            tags: [],
          },
        },
      },
      {
        run_count: 0,
        record: { id: "sample-b", kind: "synthetic", active_revision: 1 },
        revision: {
          sample_id: "sample-b",
          revision: 1,
          content_hash: `sha256:${"c".repeat(64)}`,
          actor: "operator",
          note: "",
          content: {
            display_name: "Sample B",
            aliases: [],
            artifacts: [],
            relations: [],
            status: "available",
            tags: [],
          },
        },
      },
    ],
  });
  vi.mocked(saveConfigContext).mockResolvedValue({ entry, config });
  const saved = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ConfigContextEditor
        entry={entry}
        config={config}
        operator="operator"
        onCancel={() => {}}
        onSaved={saved}
      />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: /Sample A/ });
  fireEvent.change(screen.getByLabelText("Physical sample"), { target: { value: "sample-a" } });
  fireEvent.change(screen.getByLabelText("Working point"), { target: { value: "shifted" } });
  fireEvent.change(screen.getByLabelText("Context label"), { target: { value: "A shifted" } });
  fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "4.9" } });
  expect(screen.getByLabelText("unknown")).toHaveValue(null);
  fireEvent.click(screen.getByRole("button", { name: "Save context" }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith(entry.id));
  expect(saveConfigContext).toHaveBeenCalledWith(
    expect.objectContaining({
      base: { entry_id: entry.id, content_hash: entry.content_hash },
      sample: { sample_id: "sample-a", revision: 2, role: "subject" },
      working_point_id: "shifted",
      label: "A shifted",
      parameters: {
        id: "values",
        values: [{ id: "frequency", shape: "scalar", value: { value: 4.9, unit: "GHz" } }],
      },
    }),
  );
  expect(config.parameter_snapshot.values?.[0]).toEqual({
    id: "frequency",
    shape: "scalar",
    value: { value: 4.8, unit: "GHz" },
  });
});
