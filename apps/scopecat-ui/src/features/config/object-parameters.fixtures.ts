import type { ObjectParameterContext } from "./object-parameters";
import type { ParameterDefinition } from "../../api-contract";
import type { ParameterDraftValue } from "./parameter-draft-api";

export const objectContext = (): ObjectParameterContext => {
  const sample = {
    sample_id: "chip-a",
    revision: 1,
    content_hash: "sha256:chip-a",
    actor: "author",
    note: "",
    content: {
      display_name: "Chip A",
      status: "available" as const,
      aliases: [],
      tags: [],
      relations: [],
      properties: {},
      artifacts: [],
      topology: {
        entities: [
          { id: "q0", kind: "qubit" },
          { id: "q1", kind: "qubit" },
        ],
        connections: [],
      },
    },
  };
  const target = {
    catalog_id: "catalog-a",
    target_id: "target-a",
    revision: 1,
    content_hash: "sha256:target-a",
  };
  const parameters = { revision_id: "initial", content_hash: "sha256:initial" };
  return {
    sample,
    resolution: {
      branch: { name: "daily", generation: 3, revision: parameters, actor: "author", note: "" },
      setup: { revision_id: "setup-a", content_hash: "sha256:setup-a" },
      context: {
        scenario: null,
        parameters,
        setup_content_hash: "sha256:setup-a",
        subject: {
          kind: "registered_target",
          ref: target,
          sample: {
            sample_id: sample.sample_id,
            revision: 1,
            content_hash: sample.content_hash,
            display_name: "Chip A",
            kind: "chip",
            role: "subject",
          },
          content: {
            members: [
              {
                id: "device",
                sample_id: sample.sample_id,
                revision: 1,
                content_hash: sample.content_hash,
              },
            ],
            connections: [],
          },
        },
        target_binding: {
          target,
          setup_content_hash: "sha256:setup-a",
          entities: ["q0", "q1"].map((id) => ({
            target_entity: { member_id: "device", entity_id: id },
            runtime_entity_id: id,
          })),
          connections: [],
        },
      },
    },
  };
};
export const objectDefinitions: ParameterDefinition[] = [
  {
    id: "bias",
    description: "Reviewed operating planes",
    value_type: {
      shape: "table",
      primary_key: ["profile", "qubit"],
      columns: [
        { id: "profile", value_type: { type: "string" } },
        { id: "qubit", value_type: { type: "entity", entity_kind: "qubit" } },
        { id: "peer", value_type: { type: "entity", entity_kind: "qubit" } },
        { id: "offset", value_type: { type: "quantity", finite: true, unit: "V" } },
      ],
    },
  },
];
const entity = (id: string, kind = "qubit") => ({ text: JSON.stringify({ id, kind }), unit: "" });
export const objectValues = (): ParameterDraftValue[] => [
  {
    id: "bias",
    shape: "table",
    rows: [
      {
        profile: { text: "other", unit: "" },
        qubit: entity("q1"),
        offset: { text: "10", unit: "V" },
      },
      {
        profile: { text: "cold", unit: "" },
        qubit: entity("q0"),
        offset: { text: "1e", unit: "V" },
      },
      {
        profile: { text: "warm", unit: "" },
        qubit: entity("q0"),
        peer: entity("q1"),
        offset: { text: "2", unit: "V" },
      },
      {
        profile: { text: "other-kind", unit: "" },
        qubit: entity("q0", "coupler"),
        offset: { text: "3", unit: "V" },
      },
      {
        profile: { text: "incomplete", unit: "" },
        qubit: { text: '{"id":', unit: "" },
        offset: { text: "-", unit: "V" },
      },
    ],
  },
];
