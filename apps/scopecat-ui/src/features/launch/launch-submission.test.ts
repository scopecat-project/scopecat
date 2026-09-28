import { reviewedFixture } from "../../test/scientific-fixtures";
import { expect, it } from "vitest";
import { matchesSubmissionIntent, type SubmissionRequest } from "./launch-submission";

it("matches an exact context admission without treating the active entry as its source", () => {
  const request: SubmissionRequest = {
    workspace_id: "test-source",
    scan_mode: "cartesian",
    parameter_sweeps: [],
    action: "submit",
    experiment: "signal",
    version: "1",
    request_key: "retry",
    actor: "operator",
    expected_request_hash: `sha256:${"a".repeat(64)}`,
    reviewed: reviewedFixture({
      kind: "parameter_revision",
      parameters: { revision_id: "sample-a-parked", content_hash: `sha256:${"b".repeat(64)}` },
      setup: { revision_id: "bench", content_hash: `sha256:${"e".repeat(64)}` },
      content_hash: `sha256:${"c".repeat(64)}`,
      overrides: [],
    }),
  };
  if (request.reviewed?.config_source?.kind !== "parameter_revision")
    throw new Error("Expected context");
  const intent = {
    request_hash: request.expected_request_hash,
    config_source: request.reviewed?.config_source,
  };
  expect(matchesSubmissionIntent(intent, request, request.reviewed?.binding)).toBe(true);
  expect(matchesSubmissionIntent(intent, request, undefined)).toBe(false);
  expect(
    matchesSubmissionIntent(intent, request, {
      ...request.reviewed.binding,
      setup_content_hash: `sha256:${"f".repeat(64)}`,
    }),
  ).toBe(false);
  expect(
    matchesSubmissionIntent(
      {
        ...intent,
        config_source: {
          ...request.reviewed.config_source,
          content_hash: `sha256:${"e".repeat(64)}`,
        },
      },
      request,
      request.reviewed?.binding,
    ),
  ).toBe(false);
  expect(
    matchesSubmissionIntent(
      {
        ...intent,
        config_source: {
          ...request.reviewed?.config_source,
          setup: { ...request.reviewed.config_source.setup, revision_id: "other" },
        },
      },
      request,
      request.reviewed?.binding,
    ),
  ).toBe(false);
});
