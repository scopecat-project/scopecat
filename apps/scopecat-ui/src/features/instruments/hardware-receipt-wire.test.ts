import { describe, expect, it } from "vitest";
import { decodeCollectReceipt } from "./hardware-receipt-wire";

describe("hardware receipt wire", () => {
  it("decodes typed array attachments into the existing UI measurement shape", () => {
    const float = numericAttachment(16, (view) => {
      view.setFloat64(0, 1.5, true);
      view.setFloat64(8, -2, true);
    });
    const integer = numericAttachment(16, (view) => {
      view.setBigInt64(0, 3n, true);
      view.setBigInt64(8, -4n, true);
    });
    const complex = numericAttachment(32, (view) => {
      view.setFloat64(0, 3, true);
      view.setFloat64(8, 4, true);
      view.setFloat64(16, -5, true);
      view.setFloat64(24, 12, true);
    });
    const bool = Uint8Array.of(1, 0);
    const string = stringAttachment(["scope", "猫"]);
    const receipt = decodeCollectReceipt(
      framedReceipt(
        {
          float: arrayReference(0, float, "float64", [1, 2]),
          integer: arrayReference(1, integer, "int64", [2]),
          complex: arrayReference(2, complex, "complex128", [2]),
          bool: arrayReference(3, bool, "bool", [2]),
          string: arrayReference(4, string, "string", [2]),
          missing: {
            kind: "unavailable",
            reason: "missing",
            dtype: "float64",
            unit: null,
            shape: [],
            metadata: {},
          },
        },
        [float, integer, complex, bool, string],
      ),
    );

    expect(receipt.readback?.values).toEqual({
      float: {
        kind: "array",
        dtype: "float64",
        unit: null,
        shape: [1, 2],
        metadata: {},
        values: [[1.5, -2]],
      },
      integer: {
        kind: "array",
        dtype: "int64",
        unit: null,
        shape: [2],
        metadata: {},
        values: [3, -4],
      },
      complex: {
        kind: "array",
        dtype: "complex128",
        unit: null,
        shape: [2],
        metadata: {},
        values: [
          { real: 3, imag: 4 },
          { real: -5, imag: 12 },
        ],
      },
      bool: {
        kind: "array",
        dtype: "bool",
        unit: null,
        shape: [2],
        metadata: {},
        values: [true, false],
      },
      string: {
        kind: "array",
        dtype: "string",
        unit: null,
        shape: [2],
        metadata: {},
        values: ["scope", "猫"],
      },
      missing: {
        kind: "unavailable",
        reason: "missing",
        dtype: "float64",
        unit: null,
        shape: [],
        metadata: {},
      },
    });
  });

  it("rejects a truncated attachment", () => {
    const attachment = numericAttachment(8, (view) => view.setFloat64(0, 1, true));
    const content = framedReceipt({ trace: arrayReference(0, attachment, "float64", [1]) }, [
      attachment,
    ]);

    expect(() => decodeCollectReceipt(content.slice(0, -1))).toThrow("truncated attachment");
  });
});

function arrayReference(
  index: number,
  content: Uint8Array,
  dtype: "float64" | "int64" | "complex128" | "bool" | "string",
  shape: number[],
): Record<string, unknown> {
  return {
    kind: "array_attachment",
    index,
    size_bytes: content.byteLength,
    dtype,
    unit: null,
    shape,
    metadata: {},
  };
}

function framedReceipt(
  values: Record<string, unknown>,
  attachments: Uint8Array[],
  overrides: Record<string, unknown> = {},
): ArrayBuffer {
  const header = new TextEncoder().encode(
    JSON.stringify({
      format_id: "scopecat.collect_receipt.v1",
      status: "collected",
      problems: [],
      readback: { values, metadata: {} },
      metadata: {},
      ...overrides,
    }),
  );
  const result = new Uint8Array(
    16 + header.byteLength + sum(attachments.map((item) => item.byteLength)),
  );
  result.set(new TextEncoder().encode("SCRCPT01"));
  new DataView(result.buffer).setBigUint64(8, BigInt(header.byteLength), true);
  result.set(header, 16);
  let offset = 16 + header.byteLength;
  for (const attachment of attachments) {
    result.set(attachment, offset);
    offset += attachment.byteLength;
  }
  return result.buffer;
}

function numericAttachment(size: number, write: (view: DataView) => void): Uint8Array {
  const content = new Uint8Array(size);
  write(new DataView(content.buffer));
  return content;
}

function stringAttachment(values: string[]): Uint8Array {
  const chunks = values.map((value) => new TextEncoder().encode(value));
  const offsets = new Uint8Array((values.length + 1) * 8);
  const offsetView = new DataView(offsets.buffer);
  let size = 0;
  chunks.forEach((chunk, index) => {
    offsetView.setBigUint64(index * 8, BigInt(size), true);
    size += chunk.byteLength;
  });
  offsetView.setBigUint64(values.length * 8, BigInt(size), true);
  const content = new Uint8Array(offsets.byteLength + size);
  content.set(offsets);
  let offset = offsets.byteLength;
  for (const chunk of chunks) {
    content.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return content;
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

const scalar = { kind: "scalar", dtype: "float64", value: 1.5, unit: null, metadata: {} };
const unavailable = {
  kind: "unavailable",
  dtype: "float64",
  reason: "missing",
  shape: [null],
  unit: null,
  metadata: {},
};

describe("receipt header validation", () => {
  it.each([
    { ...scalar, dtype: "invalid" },
    { ...scalar, value: "1.5" },
    { ...scalar, value: null },
    { ...scalar, unit: 1 },
    { ...scalar, metadata: [] },
    { ...scalar, dtype: "complex128", value: { real: 1 } },
    { ...unavailable, reason: "broken" },
    { ...unavailable, shape: [-1] },
    { ...unavailable, shape: ["1"] },
    { kind: "scalar" },
  ])("rejects malformed inline measurement %j", (value) => {
    expect(() => decodeCollectReceipt(framedReceipt({ value }, []))).toThrow(
      "Invalid hardware receipt",
    );
  });

  it.each([
    { ...scalar, dtype: "int64", value: -4 },
    { ...scalar, dtype: "bool", value: false },
    { ...scalar, dtype: "string", value: "猫" },
    { ...scalar, dtype: "complex128", value: { real: -1, imag: 2 } },
    scalar,
    unavailable,
    { ...unavailable, shape: [] },
    { ...unavailable, shape: [2 ** 53] },
    { kind: "scalar", dtype: "float64", value: 1.5 },
  ])("preserves valid inline measurements %j", (value) => {
    expect(decodeCollectReceipt(framedReceipt({ value }, [])).readback?.values?.value).toEqual(
      value,
    );
  });

  it.each([
    null,
    {},
    { code: "bad", message: 42, phase: "execution", related_locations: [] },
    { code: "bad", message: "bad", phase: "other", related_locations: [] },
    {
      code: "bad",
      message: "bad",
      phase: "execution",
      related_locations: [{ kind: "model", root: "x", path: false }],
    },
  ])("rejects malformed problems %j", (problem) => {
    expect(() => decodeCollectReceipt(framedReceipt({}, [], { problems: [problem] }))).toThrow(
      "Invalid hardware receipt",
    );
  });

  it("preserves problem locations and opaque details", () => {
    const problem = {
      code: "bad",
      message: "bad",
      phase: "execution",
      details: [1, "context"],
      location: { kind: "runtime", run_id: "run", point_index: 0 },
      related_locations: [
        { kind: "model", root: "plan", path: ["steps", 0] },
        { kind: "storage", path: [], ref: "data" },
        { kind: "external", uri: "file:test", path: [], row: 0, column: "A" },
      ],
    };
    expect(decodeCollectReceipt(framedReceipt({}, [], { problems: [problem] })).problems).toEqual([
      problem,
    ]);
  });
});
