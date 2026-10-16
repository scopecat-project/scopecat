import {
  LargeBinary,
  Utf8,
  List,
  Field,
  tableFromIPC,
  tableToIPC,
  vectorFromArray,
  Table,
} from "apache-arrow";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { decodeMeasurementArrowRecord } from "./measurement-arrow";

const fixture = readFileSync(
  new URL("./test-fixtures/measurement-append-v11.arrow", import.meta.url),
);

describe("live measurement Arrow", () => {
  it("decodes the current Python v10 wire including partitioned arrays", () => {
    const content = fixture.buffer.slice(
      fixture.byteOffset,
      fixture.byteOffset + fixture.byteLength,
    );

    expect(decodeMeasurementArrowRecord(content)).toEqual({
      run_id: "run-arrow-v10",
      logical_point_id: "point-7",
      point_index: 7,
      coordinates: {
        bias: {
          kind: "scalar",
          dtype: "float64",
          unit: "V",
          value: 0.25,
          metadata: { source: "setpoint" },
        },
      },
      observables: {
        trace: {
          kind: "partitioned_array",
          dtype: "float64",
          unit: "V",
          axis: 0,
          partitions: [
            {
              kind: "array",
              dtype: "float64",
              unit: "V",
              shape: [1],
              values: [1.5],
              metadata: {},
            },
            {
              kind: "array",
              dtype: "float64",
              unit: "V",
              shape: [1],
              values: [0],
              availability: {
                valid: [false],
                unavailable: [
                  {
                    reason: "invalid",
                    flat_indices: [0],
                    metadata: { sample: 1 },
                  },
                ],
              },
              metadata: {},
            },
          ],
          metadata: { channel: 1 },
        },
        entity_trace: {
          kind: "segmented_array",
          dtype: "float64",
          unit: "V",
          segments: [
            {
              kind: "array",
              dtype: "float64",
              unit: "V",
              shape: [2],
              values: [4, 0],
              availability: {
                valid: [true, false],
                unavailable: [
                  {
                    reason: "overload",
                    flat_indices: [1],
                    metadata: { entity: "q0" },
                  },
                ],
              },
              metadata: { entity: "q0" },
            },
            {
              kind: "unavailable",
              reason: "missing",
              dtype: "float64",
              unit: "V",
              shape: [null],
              metadata: { entity: "q1" },
            },
          ],
          metadata: { layout: "entity-ragged" },
        },
        missing: {
          kind: "unavailable",
          reason: "overload",
          dtype: "float64",
          unit: "V",
          shape: [null],
          metadata: { status_register: 4 },
        },
      },
      acquisition_evidence: {
        events: [
          {
            command_id: "collect-readout",
            instrument_id: "scope",
            interface_id: "test.waveform/v1",
            component_path: ["channel", "1"],
            acquisition_id: "readout-0",
            started_at: "2026-08-15T09:30:00Z",
            completed_at: "2026-08-15T09:30:01Z",
          },
        ],
        entries: [
          {
            kind: "entity",
            dimension_id: "qubit",
            acquisition: { policy: "best_effort", cohort_id: "readout-batch" },
            values: [{ kind: "instrument", event_index: 0, result_id: "q0" }, null],
          },
          { kind: "instrument", event_index: 0, result_id: "trace" },
        ],
        variable_refs: { entity_trace: 0, trace: 1 },
      },
      metadata: { note: "Python Arrow v10 fixture" },
    });
  });
});

function binary(value: unknown) {
  return vectorFromArray(
    [value === null ? null : new TextEncoder().encode(JSON.stringify(value))],
    new LargeBinary(),
  );
}

function changedFixture(change: (table: Table) => Table | void): ArrayBuffer {
  const table = tableFromIPC(fixture);
  return new Uint8Array(tableToIPC(change(table) ?? table)).buffer;
}

function jsonColumn(table: Table, name: string, value: unknown): Table {
  return table.setChild(
    name,
    vectorFromArray([new TextEncoder().encode(JSON.stringify(value))], new LargeBinary()),
  );
}

describe("Arrow boundary validation", () => {
  it.each([
    ["scopecat.variable_dtype", "decimal"],
    ["scopecat.variable_role", "unexpected"],
    ["scopecat.variable_kind", "object"],
    ["scopecat.variable_dtype", "bool"],
  ])("rejects incompatible field metadata %s=%s", (key, value) => {
    expect(() =>
      decodeMeasurementArrowRecord(
        changedFixture((table) => {
          table.schema.fields
            .find((field) => field.name === "value:bias")!
            .metadata.set(key, value);
        }),
      ),
    ).toThrow("live measurement Arrow");
  });

  it("rejects an unknown unavailable reason", () => {
    expect(() =>
      decodeMeasurementArrowRecord(
        changedFixture((table) =>
          table.setChild("unavailable_reason:missing", vectorFromArray(["broken"], new Utf8())),
        ),
      ),
    ).toThrow("live measurement Arrow");
  });

  it.each(
    [
      [{ reason: "broken", flat_indices: [0], metadata: {} }],
      [{ reason: "invalid", flat_indices: ["0"], metadata: {} }],
      [{ reason: "invalid", flat_indices: [0], metadata: [] }],
      [{ reason: "invalid", flat_indices: [1], metadata: {} }],
      [{ reason: "invalid", flat_indices: [0, 0], metadata: {} }],
      [],
    ].map((group) => [group]),
  )("rejects invalid partition availability %j", (group) => {
    expect(() =>
      decodeMeasurementArrowRecord(
        changedFixture((table) =>
          jsonColumn(table, "availability:trace", { partitions: [[], group] }),
        ),
      ),
    ).toThrow("live measurement Arrow");
  });

  it("rejects a partition shape that truncates real values", () => {
    expect(() =>
      decodeMeasurementArrowRecord(
        changedFixture((table) =>
          jsonColumn(table, "value_shape:trace", {
            shape: [1],
            partition_axis: 0,
            partition_sizes: [1],
          }).setChild(
            "availability:trace",
            vectorFromArray([new TextEncoder().encode('{"partitions":[[]]}')], new LargeBinary()),
          ),
        ),
      ),
    ).toThrow("live measurement Arrow");
  });

  it("rejects a malformed segmented sidecar", () => {
    expect(() =>
      decodeMeasurementArrowRecord(
        changedFixture((table) =>
          jsonColumn(table, "value_shape:entity_trace", { segments: "invalid", shape: [2] }),
        ),
      ),
    ).toThrow("live measurement Arrow");
  });
});

function measurementPayload(
  dtype: string,
  kind: string,
  raw: unknown,
  shape: unknown = null,
  availability: unknown = {},
): ArrayBuffer {
  const table = new Table({
    "__scopecat.logical_point_id": vectorFromArray([null], new Utf8()),
    "__scopecat.point_index": vectorFromArray([0]),
    "__scopecat.record_metadata": binary({}),
    "__scopecat.acquisition_evidence": binary({}),
    "value:test":
      dtype === "string" && kind === "array"
        ? vectorFromArray([raw], new List(new Field("item", new Utf8())))
        : vectorFromArray([raw]),
    "unavailable_reason:test": vectorFromArray([null], new Utf8()),
    "value_shape:test": binary(shape),
    "availability:test": binary(availability),
    "metadata:test": binary({}),
  });
  table.schema.metadata.set("scopecat.run_id", "test");
  const field = table.schema.fields.find((item) => item.name === "value:test")!;
  field.metadata.set("scopecat.variable_role", "observable");
  field.metadata.set("scopecat.variable_dtype", dtype);
  field.metadata.set("scopecat.variable_kind", kind);
  return new Uint8Array(tableToIPC(table)).buffer;
}

describe("valid Arrow values and sidecars", () => {
  it("round trips a sidecar edit without changing the IPC physical type", () => {
    const decoded = decodeMeasurementArrowRecord(
      changedFixture((table) => jsonColumn(table, "metadata:bias", { source: "changed" })),
    );
    expect(decoded.coordinates.bias?.metadata).toEqual({ source: "changed" });
  });

  it.each([
    ["float64", 1.5],
    ["int64", 3n],
    ["string", "猫"],
    ["bool", false],
    ["complex128", { real: -0, imag: 2 }],
  ])("decodes %s scalar values", (dtype, raw) => {
    expect(
      decodeMeasurementArrowRecord(measurementPayload(dtype, "scalar", raw)).observables.test,
    ).toMatchObject({ kind: "scalar", value: typeof raw === "bigint" ? Number(raw) : raw });
  });

  it.each([
    ["float64", [[1.5, -2]], [1, 2]],
    ["bool", [false, true], [2]],
    ["string", ["猫", ""], [2]],
    ["int64", [1n, -2n], [2]],
    ["complex128", [{ real: -0, imag: 2 }], [1]],
    ["float64", [], [0, 3]],
  ])("decodes %s arrays", (dtype, raw, shape) => {
    expect(
      decodeMeasurementArrowRecord(measurementPayload(dtype, "array", raw, { shape })).observables
        .test,
    ).toMatchObject({ kind: "array", shape });
  });

  it("preserves nullable leaves and their sparse diagnostics", () => {
    const group = { reason: "missing", flat_indices: [1], metadata: { source: "scope" } };
    expect(
      decodeMeasurementArrowRecord(
        measurementPayload("float64", "array", [1, null], { shape: [2] }, { unavailable: [group] }),
      ).observables.test,
    ).toMatchObject({
      values: [1, 0],
      availability: { valid: [true, false], unavailable: [group] },
    });
  });

  it.each([
    ["float64", "scalar", null, null],
    ["float64", "scalar", Infinity, null],
    ["int64", "scalar", 1.5, null],
    ["bool", "array", [1, 0], { shape: [2] }],
    ["float64", "array", [1], { shape: [2] }],
  ])("rejects incompatible %s %s payload", (dtype, kind, raw, shape) => {
    expect(() => decodeMeasurementArrowRecord(measurementPayload(dtype, kind, raw, shape))).toThrow(
      "live measurement Arrow",
    );
  });
});
