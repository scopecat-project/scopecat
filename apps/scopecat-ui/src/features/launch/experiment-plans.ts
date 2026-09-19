import type { MethodResponse } from "openapi-fetch";
import type { apiClient } from "../../api-client";
import type { components } from "../../api-schema";
export type PlanRevision = MethodResponse<
  typeof apiClient,
  "post",
  "/api/v1/experiment-plans/read"
>;
export type PlanDefinition = components["schemas"]["ExperimentPlanDefinition-Input"];
export type PlanRef = components["schemas"]["ExperimentPlanRef"];

export function planDifferences(before: PlanRevision, after: PlanRevision): string[] {
  const result: string[] = [];
  if (before.name !== after.name) result.push(`Name: ${before.name} → ${after.name}`);
  for (const field of [
    "experiment",
    "version",
    "inputs",
    "control_edits",
    "scan_mode",
    "parameter_sweeps",
  ] as const) {
    const left = before.definition[field];
    const right = after.definition[field];
    if (JSON.stringify(left) !== JSON.stringify(right))
      result.push(
        `${field.replaceAll("_", " ")}: ${JSON.stringify(left)} → ${JSON.stringify(right)}`,
      );
  }
  for (const [field, label] of [
    ["definition_hash", "Definition declaration"],
    ["code_revision", "Author code revision"],
    ["scientific_binding", "Scientific binding"],
    ["selection", "Scientific selection"],
    ["source", "Source analysis"],
  ] as const) {
    if (JSON.stringify(before.definition[field]) !== JSON.stringify(after.definition[field]))
      result.push(`${label} changed (exact references in details).`);
  }
  return result;
}
