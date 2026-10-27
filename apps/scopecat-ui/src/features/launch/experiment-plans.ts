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
  const labels = {
    experiment: "实验",
    version: "版本",
    inputs: "输入",
    control_edits: "控制项改动",
    scan_mode: "扫描模式",
    parameter_sweeps: "参数扫描",
  };
  if (before.name !== after.name) result.push(`名称: ${before.name} → ${after.name}`);
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
      result.push(`${labels[field]}: ${JSON.stringify(left)} → ${JSON.stringify(right)}`);
  }
  for (const [field, label] of [
    ["definition_hash", "定义声明"],
    ["code_revision", "作者源码修订"],
    ["scientific_binding", "科学绑定"],
    ["selection", "科学选择"],
    ["source", "来源分析"],
  ] as const) {
    if (JSON.stringify(before.definition[field]) !== JSON.stringify(after.definition[field]))
      result.push(`${label}已变化（精确引用见详情）。`);
  }
  return result;
}
