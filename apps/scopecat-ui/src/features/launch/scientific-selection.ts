import type { LaunchPreview } from "./launch-api";
import type { components } from "../../api-schema";
import type { ConfigContextResolution } from "../config/config-api";

export type ScientificSelection = Required<components["schemas"]["ScientificSelection-Input"]>;
export function normalizeSelection(
  selection?: components["schemas"]["ScientificSelection-Input"],
): ScientificSelection {
  return {
    subject: selection?.subject ?? { kind: "unbound" },
    configuration: selection?.configuration ?? { kind: "active" },
    batch: selection?.batch ?? { kind: "unscoped" },
  };
}
export type ScientificBinding = LaunchPreview["reviewed"]["binding"];
export const defaultSelection = (): ScientificSelection => ({
  subject: { kind: "unbound" },
  configuration: { kind: "active" },
  batch: { kind: "unscoped" },
});

export function contextSelection(resolved: ConfigContextResolution): ScientificSelection {
  const source = resolved.config_source;
  return {
    subject: {
      kind: "sample",
      sample_id: source.sample.sample_id,
      revision: source.sample.revision,
    },
    configuration: { kind: "working_point", ref: source.context, overrides: source.overrides },
    batch: source.sample.batch_id
      ? { kind: "declared", id: source.sample.batch_id }
      : { kind: "unscoped" },
  };
}

export function selectedBatch(
  selection: components["schemas"]["ScientificSelection-Input"],
): string | undefined {
  return selection.batch?.kind === "declared" ? selection.batch.id : undefined;
}

export function subjectLabel(
  selection?: components["schemas"]["ScientificSelection-Input"],
): string {
  const subject = normalizeSelection(selection).subject;
  if (subject.kind === "registered_target")
    return `Registered target ${subject.ref.target_id}, revision ${subject.ref.revision}`;
  if (subject.kind === "sample")
    return `Sample ${subject.sample_id}${subject.revision ? `, revision ${subject.revision}` : " (current revision)"}`;
  return "No sample";
}

export function subjectSample(binding?: ScientificBinding) {
  const subject = binding?.subject;
  return subject?.kind === "registered_target"
    ? subject.sample
    : subject?.kind === "inline_samples"
      ? subject.samples[0]
      : undefined;
}

// openapi-fetch's Readable projection widens tuple arrays. Validate the wire
// endpoints before returning the exact request type; never discard target evidence.
export function reviewedForRequest(
  reviewed: LaunchPreview["reviewed"],
): components["schemas"]["ReviewedScientificSelection-Input"] {
  const subject = reviewed.binding.subject;
  const binding: components["schemas"]["ResolvedScientificBinding"] = {
    ...reviewed.binding,
    subject:
      subject.kind === "registered_target"
        ? {
            ...subject,
            content: {
              ...subject.content,
              connections: subject.content.connections.map((connection) => {
                if (connection.endpoints.length !== 2)
                  throw new Error("The daemon returned a target connection without two endpoints.");
                return {
                  ...connection,
                  endpoints: [connection.endpoints[0]!, connection.endpoints[1]!],
                };
              }),
            },
          }
        : subject,
  };
  return { ...reviewed, binding };
}
