import type { components } from "../../api-schema";

export type HistoricalConfiguration = components["schemas"]["RunConfigSource-Output"];
export function historicalConfiguration(
  source: HistoricalConfiguration | null | undefined,
): components["schemas"]["ConfigurationChoice-Input"] {
  if (source?.kind === "parameter_revision")
    return {
      kind: "parameters",
      ref: source.parameters,
      setup: source.setup,
      overrides: [...source.overrides],
    };
  if (source?.kind === "analysis_candidate") return { kind: "candidate", source };
  throw new Error(
    "This run has no reusable parameter-version or candidate configuration. Its retained snapshot remains read-only; current defaults will not be substituted.",
  );
}
