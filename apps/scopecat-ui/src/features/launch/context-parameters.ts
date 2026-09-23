import type { components } from "../../api-schema";

export function contextParameterLabel(
  input: components["schemas"]["MeasurementContext"]["parameters"],
): string {
  return "revision_id" in input
    ? input.revision_id
    : `${input.proposal_id} (candidate from ${input.source_run_id})`;
}
