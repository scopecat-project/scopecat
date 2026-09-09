import { describe, expect, it } from "vitest";
import { importLaunchHandoff } from "./launch-handoff";
import { controlEdits } from "./ControlFields";
import type { LaunchCatalogEntry } from "./launch-api";
import type { LaunchDraft } from "./LaunchDraft";
import type { ComparisonHandoff } from "../analyses/RunComparison";

const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "Signal model",
  kind: "diagnostic",
  configuration_effect: "none",
  actions: ["preview", "submit"],
  request: { properties: { repetitions: { type: "number", default: 2 } }, required: [] },
  controls: [
    {
      id: "frequency",
      title: "Frequency",
      unit: "GHz",
      default: { value: 4.8, unit: "GHz" },
      minimum: 4.5,
      maximum: 5.5,
      scannable: true,
      group: "Signal",
      ownership: "editable",
      provenance: "model",
    },
  ],
};
const current: LaunchDraft = {
  controlDefinition: "old-controls",
  experiment: "signal",
  definition: "old",
  values: { repetitions: "2" },
  controls: {},
  sample: "",
  actor: "operator",
  revision: 1,
  pending: false,
  error: "",
  notice: "",
  requestKey: "old-key",
};
const handoff: ComparisonHandoff = {
  kind: "handoff",
  source_run: "retained",
  source_analysis: "fit-r1",
  source_hash: "sha256:original",
  request: {
    action: "preview",
    experiment: "signal",
    version: "1",
    request_key: "",
    actor: "operator",
    overrides: [],
    control_edits: { frequency: { mode: "fixed", value: { value: 4900, unit: "MHz" } } },
  },
};

describe("typed analysis handoff", () => {
  it("preserves exact source and values while invalidating preview/retry identity", () => {
    const draft = importLaunchHandoff(current, entry, handoff);
    expect(draft.handoff).toEqual(handoff);
    expect(draft.values.repetitions).toBe("2");
    expect(draft.requestKey).toBeUndefined();
    expect(draft.preview).toBeUndefined();
    expect(controlEdits(draft.controls).frequency).toEqual(
      handoff.request.control_edits?.frequency,
    );
    expect(current.requestKey).toBe("old-key");
  });
  it("rejects changed definition and mixed-unit scan relabeling", () => {
    expect(() => importLaunchHandoff(current, { ...entry, version: "2" }, handoff)).toThrow(
      "definition changed",
    );
    const mixed: ComparisonHandoff = {
      ...handoff,
      request: {
        ...handoff.request,
        control_edits: {
          frequency: {
            mode: "scan",
            axis: {
              kind: "values",
              values: [
                { value: 4.8, unit: "GHz" },
                { value: 4900, unit: "MHz" },
              ],
            },
          },
        },
      },
    };
    expect(() => importLaunchHandoff(current, entry, mixed)).toThrow("one explicit unit");
  });
});
