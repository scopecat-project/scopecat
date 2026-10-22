import type { LaunchDraft } from "./LaunchDraft";
import type { SubmissionRequest } from "./launch-submission";
import { defaultSelection } from "./scientific-selection";

export function comparisonFixture(): { request: SubmissionRequest; draft: LaunchDraft } {
  return {
    request: {
      action: "submit",
      workspace_id: "workspace-a",
      experiment: "signal",
      version: "1",
      actor: "Ada",
      request_key: "immutable-a",
      code_revision: { content_hash: "source-a" },
      selection: defaultSelection(),
      inputs: { count: 2, enabled: true, labels: ["x", "y"] },
      control_edits: { frequency: { mode: "fixed", value: { value: 1000, unit: "Hz" } } },
      scan_mode: "cartesian",
      parameter_sweeps: [],
    },
    draft: {
      draftId: "draft-b",
      draftName: "Draft B",
      workspaceId: "workspace-a",
      experiment: "signal",
      definition: JSON.stringify({
        version: "1",
        request: {
          properties: {
            count: { type: "integer", minimum: 1 },
            enabled: { type: "boolean" },
            labels: { type: "array", items: { type: "string", enum: ["x", "y"] } },
          },
          required: ["count"],
        },
      }),
      controlDefinition: "",
      values: { count: "2.0", enabled: "true", labels: "x\ny" },
      controls: { frequency: { mode: "fixed", value: "1", unit: "kHz" } },
      selection: defaultSelection(),
      sourceBaseline: { content_hash: "source-a" },
      actor: "Ada",
      revision: 5,
      pending: false,
      error: "",
      notice: "",
    },
  };
}
