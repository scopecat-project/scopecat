import { expect, it } from "vitest";
import { historicalConfiguration, type HistoricalConfiguration } from "./historical-configuration";

it("retains exact candidate provenance instead of resolving current defaults", () => {
  const source: HistoricalConfiguration = {
    kind: "analysis_candidate",
    source_run_id: "run-A",
    analysis_record_id: "analysis-A",
    proposal_id: "proposal-A",
    base_config_content_hash: "sha256:base",
    content_hash: "sha256:candidate",
    setup: { revision_id: "setup-A", content_hash: "sha256:setup-A" },
  };
  expect(historicalConfiguration(source)).toEqual({ kind: "candidate", source });
});

it("rejects legacy or absent sources without inventing a reusable baseline", () => {
  expect(() => historicalConfiguration(null)).toThrow("current defaults will not be substituted");
  expect(() =>
    historicalConfiguration({
      kind: "config_registry",
      selector: "old",
      entry_id: "old",
      config_ref: "old",
      content_hash: "sha256:old",
    }),
  ).toThrow("read-only");
});
