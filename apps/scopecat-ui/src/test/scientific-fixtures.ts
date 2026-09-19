import type { components } from "../api-schema";

export function reviewedFixture(
  config_source: components["schemas"]["LaunchConfigSource-Input"],
): components["schemas"]["ReviewedScientificSelection-Input"] {
  return {
    config_source,
    binding: {
      codec: "scopecat.scientific-binding.v1",
      config_content_hash: config_source.content_hash,
      setup_content_hash: `sha256:${"e".repeat(64)}`,
      subject:
        config_source.kind === "parameter_context"
          ? { kind: "inline_samples", catalog_id: "test-project", samples: [config_source.sample] }
          : { kind: "unbound" },
    },
  };
}

export const serviceWorkspaceCatalog: components["schemas"]["AuthorWorkspaceCatalog"] = {
  items: [{ id: "legacy", name: "Service code", available: true, unavailable_reason: null }],
};
