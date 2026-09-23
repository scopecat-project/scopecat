import { expect, it } from "vitest";
import type { LaunchPreview } from "./launch-api";
import { reviewedForRequest } from "./scientific-selection";
import { reviewedFixture } from "../../test/scientific-fixtures";

it("preserves target connection endpoints when reviewed evidence becomes a request", () => {
  const reviewed: LaunchPreview["reviewed"] = reviewedFixture({
    kind: "config_registry",
    selector: "active",
    entry_id: "base",
    config_ref: "base",
    content_hash: `sha256:${"a".repeat(64)}`,
    registry_generation: 1,
  });
  reviewed.binding.subject = {
    kind: "registered_target",
    ref: {
      catalog_id: "lab",
      target_id: "device",
      revision: 1,
      content_hash: `sha256:${"b".repeat(64)}`,
    },
    content: {
      members: [
        {
          id: "chip",
          sample_id: "device-a",
          revision: 1,
          content_hash: `sha256:${"c".repeat(64)}`,
        },
      ],
      connections: [
        {
          id: "link",
          kind: "coupling",
          endpoints: [
            { member_id: "chip", entity_id: "q0" },
            { member_id: "chip", entity_id: "q1" },
          ],
        },
      ],
    },
    sample: {
      role: "subject",
      sample_id: "device-a",
      revision: 1,
      content_hash: `sha256:${"c".repeat(64)}`,
      kind: "chip",
      display_name: "A",
    },
  };
  expect(reviewedForRequest(reviewed)).toEqual(reviewed);
  reviewed.binding.subject.content.connections[0]!.endpoints.pop();
  expect(() => reviewedForRequest(reviewed)).toThrow("without two endpoints");
});
