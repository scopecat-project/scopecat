// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { components } from "../../api-schema";
import { PreflightSummary } from "./PreflightSummary";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";

afterEach(cleanup);
const entry: LaunchCatalogEntry = {
  id: "candidate",
  version: "1",
  title: "Candidate",
  description: "Compile candidate",
  kind: "calibration",
  configuration_effect: "candidate",
  controls: [],
  actions: ["preview", "submit"],
  request: {},
};
const preview: LaunchPreview = {
  experiment_id: "candidate",
  request_hash: "sha256:" + "a".repeat(64),
  point_count: 2,
  config_source: {
    kind: "config_registry",
    selector: "active",
    entry_id: "baseline",
    config_ref: "baseline",
    content_hash: "sha256:" + "b".repeat(64),
    registry_generation: 1,
  },
  summary: "Source and proposed candidate",
  resolved_inputs: {},
  controls: [],
  resources: [],
};
const stage: components["schemas"]["PreflightStage"] = {
  instrument_ids: [],
  inspections: [],
  planned_settings: [],
  planned_setting_limit: 64,
  planned_settings_truncated: false,
  id: "candidate",
  label: "Proposed candidate run",
  experiment_id: "ramsey",
  configuration: "proposed_candidate",
  config_content_hash: "sha256:" + "c".repeat(64),
  configuration_meaning: "Proposed configuration; not yet verified.",
  executions: { kind: "exact", value: 1, unit: "runs", basis: "Declared stage" },
  point_scope: "static_plan",
  initial_proposed_points: 2,
  points_per_execution: { kind: "exact", value: 2, unit: "points", basis: "Static plan" },
  shots_per_point_per_entity: {
    kind: "exact",
    value: 64,
    unit: "shots",
    basis: "Invocation shots",
  },
  entity_ids: ["q0", "q1"],
  products: [
    {
      id: "iq",
      retention: "retained",
      dtype: "complex128",
      unit: "V",
      dims: ["point", "entity", "shot"],
      shape: [2, 2, 64],
    },
    {
      id: "raw",
      retention: "transient",
      dtype: "float64",
      unit: "V",
      dims: ["shot"],
      shape: [null],
    },
  ],
  costs: [
    {
      metric: "batch_point_capacity",
      scope: "inspected_artifact",
      target_id: "public-target",
      quantity: {
        kind: "bounded",
        lower: 0,
        upper: 32,
        unit: "points",
        basis: "Target capacity only",
      },
    },
    {
      metric: "wall_time",
      scope: "experiment",
      quantity: { kind: "unknown", unit: "s", basis: "No physical duration estimate" },
    },
  ],
  sampled_point_limit: 64,
  sampled_points: 2,
  selected_point_limit: 1,
  selected_points: 1,
};

it("separates exact stage scope, target bounds, unknown time and retention", () => {
  render(
    <PreflightSummary
      entry={entry}
      preview={{
        ...preview,
        preflight: {
          stages: [stage],
          scope_basis: "Source plus candidate; point count is not a procedure total.",
        },
      }}
    />,
  );
  expect(screen.getByText(/2 initial points in the first experiment/)).toBeVisible();
  expect(screen.getByText("Exact: 64 shots")).toBeVisible();
  expect(screen.getByText("Bounded: 0–32 points")).toBeVisible();
  expect(screen.getByText("Unknown (s)")).toBeVisible();
  expect(screen.getByText(/Target: public-target/)).toBeVisible();
  expect(screen.getByText("Transient (per point)")).toBeVisible();
  expect(screen.getByText("point: 2 × entity: 2 × shot: 64")).toBeVisible();
  expect(screen.getByText("shot: unknown")).toBeVisible();
  expect(screen.getByText("Proposed configuration; not yet verified.")).toBeVisible();
  expect(screen.getByText(/2\/64 displayed points; 1\/1 selected points/)).toBeVisible();
  expect(screen.queryByText("0 s")).not.toBeInTheDocument();
});

it("reports an absent project summary without inventing zero work", () => {
  render(<PreflightSummary entry={entry} preview={preview} />);
  expect(screen.getByText(/Detailed preflight not provided/)).toBeVisible();
  expect(screen.queryByText(/Exact:/)).not.toBeInTheDocument();
});

it("shows planned settings with unit, order and truncation without claiming readback", () => {
  render(
    <PreflightSummary
      entry={entry}
      preview={{
        ...preview,
        preflight: {
          scope_basis: "Selected frozen plan",
          stages: [
            {
              ...stage,
              planned_setting_limit: 64,
              planned_settings_truncated: true,
              planned_settings: [
                {
                  point_index: 3,
                  proposal_fingerprint: "proposal",
                  operation_index: 2,
                  assignment_index: 0,
                  operation_id: "setup",
                  instrument_id: "signal-source",
                  setting: {
                    target: {
                      kind: "interface",
                      interface_id: "rf-output/v1",
                      component_path: ["channels", "1"],
                      property_id: "frequency",
                    },
                    value: { value: 5000000000, unit: "Hz" },
                  },
                },
              ],
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByRole("region", { name: "Planned instrument settings" })).toBeVisible();
  expect(screen.getByText("5000000000 Hz")).toBeVisible();
  expect(screen.getByText(/Point 3.*3.1/)).toBeVisible();
  expect(screen.getByText(/signal-source.*channels\/1.*frequency/)).toBeVisible();
  expect(screen.getByText(/not observed or confirmed state/)).toBeVisible();
  expect(screen.getByText(/first 64 planned settings.*omitted/)).toBeVisible();
});

it("distinguishes an uninspected point from an inspected point with no settings", () => {
  const view = render(
    <PreflightSummary
      entry={entry}
      preview={{
        ...preview,
        preflight: {
          scope_basis: "No selected point",
          stages: [{ ...stage, selected_points: 0 }],
        },
      }}
    />,
  );
  expect(screen.getByText(/No point was inspected.*settings are unknown/)).toBeVisible();
  expect(screen.queryByText("No instrument settings in the selected point.")).toBeNull();
  view.rerender(
    <PreflightSummary
      entry={entry}
      preview={{
        ...preview,
        preflight: {
          scope_basis: "Selected point",
          stages: [stage],
        },
      }}
    />,
  );
  expect(screen.getByText("No instrument settings in the selected point.")).toBeVisible();
});
