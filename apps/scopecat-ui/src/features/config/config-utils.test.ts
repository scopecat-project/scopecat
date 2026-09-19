import { describe, expect, it } from "vitest";
import type { ConfigRegistryEntry } from "../../api-contract";
import { configSourceLabel, filterConfigEntries } from "./config-utils";

const HASH = `sha256:${"a".repeat(64)}`;

const sample = {
  sample_id: "chip-a",
  revision: 1,
  content_hash: "sha256:sample",
  role: "primary",
  kind: "chip",
  display_name: "Chip A",
};

describe("calibration cohort registry presentation", () => {
  const entry: ConfigRegistryEntry = {
    id: "drag-merged",
    config_ref: "entries/drag-merged.json",
    content_hash: HASH,
    actor: "resident-worker",
    note: "nightly calibration",
    recorded_at: "2026-08-19T01:00:00Z",
    source: {
      kind: "parameter_context",
      context: {
        workspace_id: "workspace-q0",
        sample,
        working_point_id: "parked",
        label: "Q0 parked",
        base: { entry_id: "baseline", content_hash: "sha256:baseline" },
        value_origins: [],
      },
      publication: {
        kind: "calibration_cohort_merge",
        cohort_id: "drag-nightly",
        spec_hash: HASH,
        composition_policy_ref: {
          id: "reference_lab.drag-composition",
          version: "1",
          fingerprint: HASH,
        },
        merge_policy: "common_base_cells_v1",
        base: {
          kind: "config_registry",
          entry_id: "baseline",
          config_ref: "baseline.json",
          content_hash: HASH,
          scope: { kind: "working_point", workspace_id: "workspace-q0", sample },
        },
        candidate_id: "drag-candidate",
        contributions: [
          {
            member_id: "q0",
            proof: {
              kind: "verified_parameter_proposal_v1",
              evidence_step: {
                procedure_run_id: "procedure-q0",
                step_key: "verification",
                attempt: 1,
              },
              baseline_run_id: "baseline-run-q0",
              fit_analysis_record_id: "fit-analysis-q0",
              proposal_id: "proposal-q0",
              candidate_run_id: "candidate-run-q0",
              decision: {
                analysis_record_id: "verification-analysis-q0",
                output_id: "decision",
                schema_id: "reference_lab.drag-decision.v1",
                schema_hash: HASH,
              },
            },
            result_input_fingerprint: HASH,
          },
        ],
      },
    },
  };

  it("has its own source label", () => {
    expect(configSourceLabel(entry)).toBe("Calibration cohort merge");
  });

  it.each([
    "drag-nightly",
    "reference_lab.drag-composition",
    "q0",
    "procedure-q0",
    "verification",
    "proposal-q0",
    "fit-analysis-q0",
    "verified_parameter_proposal_v1",
  ])("is searchable by exact provenance term %s", (term) => {
    expect(filterConfigEntries([entry], term)).toEqual([entry]);
  });
});
