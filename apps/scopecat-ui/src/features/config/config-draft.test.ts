import { describe, expect, it } from "vitest";
import { deriveConfigDraftUpdates } from "./config-draft";
import type {
  ConfigProfileSnapshot,
  ParameterEntity,
  StoredParameterValue,
} from "../../api-contract";

describe("deriveConfigDraftUpdates", () => {
  it("uses semantic keyed row operations and ignores entity metadata in keys", () => {
    const config = testConfig();
    const edited: StoredParameterValue = {
      id: "calibration",
      shape: "table",
      rows: [
        {
          entity: entity("q0", { display: "new label" }),
          frequency: 6.6,
        },
        {
          entity: entity("q2"),
          frequency: 6.8,
        },
      ],
    };

    expect(deriveConfigDraftUpdates(config, { calibration: edited })).toEqual([
      {
        kind: "delete_parameter_rows",
        parameter_id: "calibration",
        key: { entity: entity("q1") },
      },
      {
        kind: "update_parameter_rows",
        parameter_id: "calibration",
        key: { entity: entity("q0", { display: "new label" }) },
        values: { frequency: 6.6 },
      },
      {
        kind: "insert_parameter_rows",
        parameter_id: "calibration",
        rows: [
          {
            entity: entity("q2"),
            frequency: 6.8,
          },
        ],
      },
    ]);
  });

  it("emits complete replacement for an unkeyed table", () => {
    const config = testConfig();
    const edited: StoredParameterValue = {
      id: "flags",
      shape: "table",
      rows: [{ name: "drive", enabled: false }],
    };

    expect(deriveConfigDraftUpdates(config, { flags: edited })).toEqual([
      { kind: "replace_parameter", value: edited },
    ]);
  });

  it("keeps duplicate edited keys in a replacement for daemon validation", () => {
    const config = testConfig();
    const base = config.parameter_snapshot.values?.find((value) => value.id === "calibration");
    if (base?.shape !== "table") throw new Error("missing table fixture");
    const duplicate = {
      entity: entity("q0", { attempted: true }),
      frequency: 7.1,
    };
    const edited: StoredParameterValue = {
      ...base,
      rows: [...(base.rows ?? []), duplicate],
    };

    expect(deriveConfigDraftUpdates(config, { calibration: edited })).toEqual([
      {
        kind: "replace_parameter",
        value: edited,
      },
    ]);
  });

  it("does not emit an unchanged scalar value", () => {
    const config = testConfig();
    const edited: StoredParameterValue = {
      id: "enabled",
      shape: "scalar",
      value: true,
    };

    expect(deriveConfigDraftUpdates(config, { enabled: edited })).toEqual([]);
  });
});

function testConfig(): ConfigProfileSnapshot {
  const entities = [entity("q0"), entity("q1"), entity("q2")];
  return {
    id: "active",
    system: {
      id: "system",
      topology: {
        entities,
      },
      instrument_registry: { instruments: [] },
      routing: { roles: [], routes: [] },
      domain_target: null,
      parameter_catalog: {
        id: "parameters",
        definitions: [
          {
            id: "enabled",
            value_type: {
              shape: "scalar",
              atom: { type: "bool" },
            },
          },
          {
            id: "calibration",
            value_type: {
              shape: "table",
              columns: [
                {
                  id: "entity",
                  value_type: {
                    type: "entity",
                    entity_kind: "logical_qubit",
                  },
                },
                {
                  id: "frequency",
                  value_type: {
                    type: "float",
                    finite: true,
                  },
                },
              ],
              primary_key: ["entity"],
            },
          },
          {
            id: "flags",
            value_type: {
              shape: "table",
              columns: [
                {
                  id: "name",
                  value_type: {
                    type: "string",
                  },
                },
                {
                  id: "enabled",
                  value_type: { type: "bool" },
                },
              ],
              primary_key: [],
            },
          },
        ],
      },
    },
    parameter_snapshot: {
      id: "parameters",
      values: [
        {
          id: "enabled",
          shape: "scalar",
          value: true,
        },
        {
          id: "calibration",
          shape: "table",
          rows: [
            {
              entity: entity("q0", { display: "base label" }),
              frequency: 6.5,
            },
            {
              entity: entity("q1"),
              frequency: 6.7,
            },
          ],
        },
        {
          id: "flags",
          shape: "table",
          rows: [{ name: "drive", enabled: true }],
        },
      ],
    },
  };
}

function entity(id: string, metadata: Record<string, string | boolean> = {}): ParameterEntity {
  return {
    id,
    kind: "logical_qubit",
    metadata,
  };
}
