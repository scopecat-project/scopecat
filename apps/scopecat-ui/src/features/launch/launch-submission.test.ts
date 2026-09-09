import { expect, it } from "vitest";
import { matchesSubmissionIntent, type SubmissionRequest } from "./launch-submission";

it("matches an exact context admission without treating the active entry as its source", () => {
  const request: SubmissionRequest = {
    overrides: [],
    action: "submit",
    experiment: "signal",
    version: "1",
    request_key: "retry",
    actor: "operator",
    expected_request_hash: `sha256:${"a".repeat(64)}`,
    config_source: {
      kind: "parameter_context",
      context: { entry_id: "sample-a-parked", content_hash: `sha256:${"b".repeat(64)}` },
      content_hash: `sha256:${"c".repeat(64)}`,
      lab_generation: 4,
      sample: {
        sample_id: "a",
        revision: 2,
        content_hash: `sha256:${"d".repeat(64)}`,
        role: "subject",
        kind: "synthetic",
        display_name: "A",
        context_id: "parked",
      },
      overrides: [],
    },
  };
  if (request.config_source?.kind !== "parameter_context") throw new Error("Expected context");
  const intent = {
    request_hash: request.expected_request_hash,
    config_source: request.config_source,
  };
  expect(matchesSubmissionIntent(intent, request)).toBe(true);
  expect(
    matchesSubmissionIntent(
      { ...intent, config_source: { ...request.config_source, lab_generation: 5 } },
      request,
    ),
  ).toBe(false);
  expect(
    matchesSubmissionIntent(
      {
        ...intent,
        config_source: {
          ...request.config_source,
          sample: { ...request.config_source.sample, revision: 3 },
        },
      },
      request,
    ),
  ).toBe(false);
});
