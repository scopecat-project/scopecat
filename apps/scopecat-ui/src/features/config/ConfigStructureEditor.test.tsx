// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ConfigEntryInspector } from "./ConfigEntryInspector";
import { ConfigStructureEditor } from "./ConfigStructureEditor";
import {
  previewConfigStructure,
  saveConfigContext,
  type ConfigRegistryEntryDetail,
} from "./config-api";
vi.mock("./config-api", async (original) => ({
  ...(await original<typeof import("./config-api")>()),
  previewConfigStructure: vi.fn(),
  saveConfigContext: vi.fn(),
}));
afterEach(cleanup);
const hash = `sha256:${"a".repeat(64)}`;
const detail: ConfigRegistryEntryDetail = {
  structureVersion: hash,
  summary: { id: "lab", primaryEntityId: "q0", parameterCount: 1, instrumentCount: 0 },
  entry: {
    id: "old",
    content_hash: hash,
    config_ref: "old.json",
    actor: "author",
    note: "",
    source: {
      kind: "parameter_context",
      context: {
        value_origins: [],
        sample: {
          sample_id: "sample-a",
          revision: 1,
          content_hash: hash,
          role: "subject",
          kind: "synthetic",
          display_name: "A",
        },
        working_point_id: "parked",
        label: "A parked",
        base: { entry_id: "base", content_hash: hash },
      },
    },
  },
  config: {
    id: "lab",
    system: {
      id: "system",
      domain_target: null,
      primary_entity_id: "q0",
      topology: { entities: [] },
      instrument_registry: { instruments: [] },
      parameter_catalog: {
        id: "catalog",
        definitions: [
          {
            id: "observations",
            value_type: {
              shape: "table",
              primary_key: ["sample"],
              columns: [{ id: "sample", value_type: { type: "string" } }],
            },
          },
        ],
      },
    },
    parameter_snapshot: {
      id: "values",
      values: [{ id: "observations", shape: "table", rows: [{ sample: "a" }, { sample: "b" }] }],
    },
  },
};

it("previews an optional column without invented values, saves exact old sample, and invalidates changed preview", async () => {
  vi.mocked(previewConfigStructure).mockImplementation(async (plan) => ({
    config: detail.config,
    origin: { before_version: hash, after_version: hash, edits: plan.edits },
    impacts: [],
    missing_values: ["observations[0].quality", "observations[1].quality"],
    cell_mappings: [],
    consumers: [],
    consumer_scope: "Only explicit dependencies are checked.",
  }));
  vi.mocked(saveConfigContext).mockResolvedValue({ entry: detail.entry, config: detail.config });
  const onSaved = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}
    >
      <ConfigStructureEditor
        detail={detail}
        operator="operator"
        onCancel={vi.fn()}
        onSaved={onSaved}
      />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("Structure column ID"), { target: { value: "quality" } });
  fireEvent.change(screen.getByLabelText("Structure note"), {
    target: { value: "New optional analysis column" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview structure change" }));
  await screen.findByLabelText("Structure impact preview");
  expect(vi.mocked(previewConfigStructure).mock.calls[0]?.[0]).toEqual(
    expect.objectContaining({
      edits: [
        expect.objectContaining({
          kind: "add_column",
          values: [
            expect.objectContaining({ key: { sample: "a" }, value: null, origin: "unknown" }),
            expect.objectContaining({ key: { sample: "b" }, value: null, origin: "unknown" }),
          ],
        }),
      ],
    }),
  );
  fireEvent.change(screen.getByLabelText("Structure value origin"), {
    target: { value: "imported" },
  });
  expect(screen.getByRole("button", { name: "Save revised working point" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview structure change" }));
  await screen.findByLabelText("Structure impact preview");
  fireEvent.change(screen.getByLabelText("Structure note"), {
    target: { value: "Revised source description" },
  });
  expect(screen.getByRole("button", { name: "Save revised working point" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview structure change" }));
  await screen.findByLabelText("Structure impact preview");
  fireEvent.change(screen.getByLabelText("Structure column ID"), {
    target: { value: "quality_v2" },
  });
  expect(screen.getByRole("button", { name: "Save revised working point" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview structure change" }));
  await screen.findByLabelText("Structure impact preview");
  fireEvent.click(screen.getByRole("button", { name: "Save revised working point" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith("old"));
  expect(saveConfigContext).toHaveBeenCalledWith(
    expect.objectContaining({
      sample: { sample_id: "sample-a", revision: 1, role: "subject" },
      structure_plan: expect.objectContaining({ base: { entry_id: "old", content_hash: hash } }),
    }),
  );
});

it("shows a declared unknown reason and the old source cell on a saved working point", () => {
  if (detail.entry.source.kind !== "parameter_context") throw new Error("Expected context fixture");
  const entry = {
    ...detail.entry,
    source: {
      ...detail.entry.source,
      context: {
        ...detail.entry.source.context,
        value_origins: [
          {
            parameter_id: "observations",
            field_id: "quality",
            key: { sample: "b" },
            layer: "context" as const,
            entry: { entry_id: "old", content_hash: hash },
            evidence: {
              origin: "unknown" as const,
              value: null,
              note: "Sample B has not been evaluated",
              key: { specimen: "b" },
            },
            source_cell: {
              entry: { entry_id: "original", content_hash: hash },
              parameter_id: "observations",
              field_id: "prior_quality",
              key: { specimen: "b" },
            },
          },
        ],
      },
    },
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ConfigEntryInspector
        entry={entry}
        active={false}
        snapshotPending={false}
        snapshotError={null}
        note=""
        pending={false}
        actionDisabled
        onNoteChange={vi.fn()}
        onSelectEntry={vi.fn()}
        onActivate={vi.fn()}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText(/^Detailed cell origins/));
  expect(screen.getByText("unknown · Sample B has not been evaluated")).toBeVisible();
  expect(screen.getByText(/Source: original.*observations/)).toHaveTextContent("prior_quality");
  expect(screen.getByText(/Source: original.*observations/)).toHaveTextContent("specimen");
});
