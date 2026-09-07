import { describe, expect, it } from "vitest";
import type { ConfigProfileSnapshot, ParameterAtom } from "../../api-contract";
import { diffConfigParameters, parameterAtomLabel, parameterTypeLabel } from "./config-diff";

describe("typed config parameter diff", () => {
  it("compares keyed tables by row identity and reports changed cells", () => {
    const active = configSnapshot({
      scalar: { value: 5, unit: "GHz" },
      table: [row("q0", 6.5, { source: "active" }), row("q1", 6.7)],
    });
    const selected = configSnapshot({
      scalar: { value: 5.1, unit: "GHz" },
      table: [row("q0", 6.6, { source: "selected" }), row("q2", 6.8)],
    });

    const diffs = diffConfigParameters(active, selected);

    expect(diffs.find((item) => item.parameterId === "drive.frequency")).toMatchObject({
      status: "changed",
      before: { value: { value: 5, unit: "GHz" } },
      after: { value: { value: 5.1, unit: "GHz" } },
    });
    const table = diffs.find((item) => item.parameterId === "qubits");
    expect(table?.table?.mode).toBe("keyed");
    expect(table?.table?.rows.map(({ status }) => status)).toEqual(["changed", "added", "removed"]);
    expect(
      table?.table?.rows[0]?.cells.find((cell) => cell.columnId === "readout_frequency"),
    ).toMatchObject({
      status: "changed",
      before: { value: 6.5, unit: "GHz" },
      after: { value: 6.6, unit: "GHz" },
    });
    expect(table?.table?.rows[0]?.key).toEqual({
      qubit: {
        id: "q0",
        kind: "logical_qubit",
        metadata: { source: "selected" },
      },
    });
  });

  it("treats a changed table without a primary key as a complete replacement", () => {
    const active = configSnapshot({
      scalar: { value: 5, unit: "GHz" },
      table: [row("q0", 6.5)],
      primaryKey: [],
    });
    const selected = configSnapshot({
      scalar: { value: 5, unit: "GHz" },
      table: [row("q0", 6.6)],
      primaryKey: [],
    });

    const table = diffConfigParameters(active, selected).find(
      (item) => item.parameterId === "qubits",
    );

    expect(table).toMatchObject({
      status: "changed",
      table: { mode: "complete-replacement", rows: [] },
    });
  });

  it("formats quantity, entity, and typed schema labels for the UI", () => {
    const config = configSnapshot({
      scalar: { value: 5, unit: "GHz" },
      table: [row("q0", 6.5)],
    });
    const tableDefinition = config.system.parameter_catalog.definitions?.[1];

    expect(parameterAtomLabel({ value: 6.5, unit: "GHz" })).toBe("6.5 GHz");
    expect(
      parameterAtomLabel({
        id: "q0",
        kind: "logical_qubit",
        metadata: {},
      }),
    ).toBe("q0 (logical_qubit)");
    expect(parameterTypeLabel(tableDefinition)).toBe("Table · 2 columns");
  });
});

function configSnapshot({
  scalar,
  table,
  primaryKey = ["qubit"],
}: {
  scalar: { value: number; unit: string };
  table: Array<Record<string, ParameterAtom>>;
  primaryKey?: string[];
}): ConfigProfileSnapshot {
  return {
    id: `profile-${scalar.value}-${table.length}`,
    system: {
      id: "system",
      primary_entity_id: "q0",
      topology: {
        entities: [],
      },
      instrument_registry: { instruments: [] },
      routing: { roles: [], routes: [] },
      domain_target: null,
      parameter_catalog: {
        id: "catalog",
        definitions: [
          {
            id: "drive.frequency",
            value_type: {
              shape: "scalar",
              atom: { type: "quantity", finite: true, unit: "GHz" },
            },
          },
          {
            id: "qubits",
            value_type: {
              shape: "table",
              columns: [
                {
                  id: "qubit",
                  value_type: {
                    type: "entity",
                    entity_kind: "logical_qubit",
                  },
                },
                {
                  id: "readout_frequency",
                  value_type: { type: "quantity", finite: true, unit: "GHz" },
                },
              ],
              primary_key: primaryKey,
            },
          },
        ],
      },
    },
    parameter_snapshot: {
      id: "parameters",
      values: [
        {
          id: "drive.frequency",
          shape: "scalar",
          value: scalar,
        },
        {
          id: "qubits",
          shape: "table",
          rows: table,
        },
      ],
    },
  };
}

function row(
  id: string,
  frequency: number,
  metadata: Record<string, string> = {},
): Record<string, ParameterAtom> {
  return {
    qubit: {
      id,
      kind: "logical_qubit",
      metadata,
    },
    readout_frequency: { value: frequency, unit: "GHz" },
  };
}

it("retains equivalent unit edits while matching reordered entities", () => {
  const active = configSnapshot({
    scalar: { value: 5, unit: "GHz" },
    table: [row("q0", 6.5), row("q1", 6.7)],
  });
  const selected = configSnapshot({
    scalar: { value: 5, unit: "GHz" },
    table: [row("q1", 6.7), { ...row("q0", 6.5), readout_frequency: { value: 6500, unit: "MHz" } }],
  });
  const table = diffConfigParameters(active, selected).find(
    (item) => item.parameterId === "qubits",
  )?.table;
  expect(table?.rows.map((item) => item.status)).toEqual(["unchanged", "changed"]);
  expect(table?.rows[1]?.cells.find((item) => item.columnId === "readout_frequency")).toMatchObject(
    {
      status: "changed",
      changeKind: "representation",
      before: { value: 6.5, unit: "GHz" },
      after: { value: 6500, unit: "MHz" },
    },
  );
});
