import type { SoftwareExecutionScenario } from "../api-contract";

export const scenarioFixture: SoftwareExecutionScenario = {
  kind: "software",
  id: "synthetic-resonance",
  label: "Synthetic resonance",
  model_id: "reference.resonance",
  model_version: "1",
  seed: 17,
  settings: { linewidth: 0.2 },
  capabilities: ["Resonance frequency sweep"],
  limitations: ["Does not model device heating"],
};
