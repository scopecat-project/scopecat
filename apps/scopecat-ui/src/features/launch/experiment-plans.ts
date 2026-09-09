import type { components } from "../../api-schema";
export type PlanRevision = components["schemas"]["ExperimentPlanRevision"];
export type PlanDefinition = components["schemas"]["ExperimentPlanDefinition-Input"];
export type PlanRef = components["schemas"]["ExperimentPlanRef"];

function sampleLabel(plan: PlanRevision): string {
  return plan.definition.sample
    ? `${plan.definition.sample.display_name}, revision ${plan.definition.sample.revision}`
    : "No sample";
}

export function planDifferences(before: PlanRevision, after: PlanRevision): string[] {
  const result: string[] = [];
  if (before.name !== after.name) result.push(`Name: ${before.name} → ${after.name}`);
  for (const field of ["experiment", "version", "inputs", "control_edits", "overrides"] as const) {
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
    ["configuration", "Saved configuration"],
    ["context", "Sample working point"],
    ["source", "Source analysis"],
  ] as const) {
    if (JSON.stringify(before.definition[field]) !== JSON.stringify(after.definition[field]))
      result.push(`${label} changed (exact references in details).`);
  }
  if (JSON.stringify(before.definition.sample) !== JSON.stringify(after.definition.sample)) {
    result.push(`Sample: ${sampleLabel(before)} → ${sampleLabel(after)}`);
  }
  return result;
}
