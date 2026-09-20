import type { components } from "../api-schema";

export function problemLocationLabel(location: components["schemas"]["ProblemLocation"]): string {
  const prefix = "root" in location ? location.root : location.kind;
  return "path" in location && location.path.length > 0
    ? `${prefix}.${location.path.join(".")}`
    : prefix;
}
