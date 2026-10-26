import { describe, expect, it } from "vitest";
import {
  decisionFields,
  decisionMetadata,
  decisionFormValue,
  decisionValueError,
  decisionConstraintError,
} from "./DecisionFields";

const structure = {
  type: "object",
  fields: {
    outcome: { type: "literal", values: ["Peak selected", "No clear peak", "Unsure"] },
    frequency_mhz: { type: "union", variants: [{ type: "float" }, { type: "null" }] },
  },
};
const metadata = {
  field_labels: { frequency_mhz: "Peak frequency (MHz)" },
  field_constraints: {
    frequency_mhz: {
      minimum: 6490,
      maximum: 6510,
      required_when: { field: "outcome", value: "Peak selected" },
      empty_otherwise: true,
    },
  },
};

describe("nullable numeric decisions", () => {
  it("converts blank optional input to null, preserves invalid edits, and never invents zero", () => {
    const fields = decisionFields(structure)!;
    expect(fields).toBeDefined();
    for (const text of ["", "  "]) {
      const value = decisionFormValue(fields, { outcome: "Unsure", frequency_mhz: text });
      expect(value).toEqual({ outcome: "Unsure", frequency_mhz: null });
      expect(decisionValueError(fields, value)).toBeUndefined();
    }
    for (const text of ["-", "6500x", "Infinity"]) {
      const value = decisionFormValue(fields, { outcome: "Peak selected", frequency_mhz: text });
      expect(value).toHaveProperty("frequency_mhz", text);
      expect(decisionValueError(fields, value)).toBeDefined();
    }
    expect(decisionFormValue(fields, { frequency_mhz: "6500.25" })).toEqual({
      frequency_mhz: 6500.25,
    });
  });
  it.each([6490, 6500, 6510])("accepts an in-range selection %s", (frequency) => {
    expect(
      decisionConstraintError({ outcome: "Peak selected", frequency_mhz: frequency }, metadata),
    ).toBeUndefined();
  });
  it.each(["Unsure", "No clear peak"])("accepts %s only without a frequency", (outcome) => {
    expect(decisionConstraintError({ outcome, frequency_mhz: null }, metadata)).toBeUndefined();
    expect(decisionConstraintError({ outcome, frequency_mhz: 6500 }, metadata)).toMatch(
      /其他情况请留空/,
    );
  });
  it("reports the missing or out-of-range field before submission", () => {
    expect(
      decisionConstraintError({ outcome: "Peak selected", frequency_mhz: null }, metadata),
    ).toMatch(/请填写 Peak frequency/);
    expect(
      decisionConstraintError({ outcome: "Peak selected", frequency_mhz: 6600 }, metadata),
    ).toContain("6490 至 6510 之间");
  });
  it("rejects unsupported unions rather than treating them as a number", () => {
    expect(
      decisionFields({
        type: "object",
        fields: { x: { type: "union", variants: [{ type: "float" }, { type: "string" }] } },
      }),
    ).toBeUndefined();
  });
});

describe("numeric conditional controls", () => {
  const fields = decisionFields({
    type: "object",
    fields: {
      mode: { type: "int" },
      detail: { type: "union", variants: [{ type: "float" }, { type: "null" }] },
    },
  })!;
  const numericMetadata = {
    field_constraints: {
      detail: { required_when: { field: "mode", value: 1 }, empty_otherwise: true },
    },
  };
  it("uses the normalized numeric choice for serialization and validation", () => {
    const value = decisionFormValue(fields, { mode: "1", detail: "2" }, numericMetadata);
    expect(value).toEqual({ mode: 1, detail: 2 });
    expect(decisionValueError(fields, value)).toBeUndefined();
    expect(decisionConstraintError(value, numericMetadata)).toBeUndefined();
    expect(decisionFormValue(fields, { mode: "0", detail: "2" }, numericMetadata)).toEqual({
      mode: 0,
      detail: null,
    });
  });
  it.each(["", " ", "-", "1e", "1.5", "invalid", null])(
    "preserves dependent input for incomplete/invalid control %s",
    (mode) => {
      const value = decisionFormValue(fields, { mode, detail: "2" }, numericMetadata);
      expect(value).toHaveProperty("detail", 2);
      expect(decisionValueError(fields, value)).toBeDefined();
    },
  );
});

it("uses opt-in rules over presentation hints with the same answer boundary matrix", () => {
  const fields = decisionFields(structure)!;
  const rules = decisionMetadata(
    { field_constraints: {} },
    { version: 1, fields: metadata.field_constraints },
  );
  for (const [frequency, valid] of [
    [6490, true],
    [6510, true],
    [6489, false],
    [6511, false],
    [null, false],
  ] as const) {
    const selected = decisionFormValue(
      fields,
      { outcome: "Peak selected", frequency_mhz: frequency },
      rules,
    );
    expect(!decisionValueError(fields, selected) && !decisionConstraintError(selected, rules)).toBe(
      valid,
    );
    expect(
      decisionFormValue(fields, { outcome: "Unsure", frequency_mhz: frequency }, rules),
    ).toEqual({ outcome: "Unsure", frequency_mhz: null });
  }
  const omitted = decisionFormValue(fields, { outcome: "Unsure" }, rules);
  expect(decisionValueError(fields, omitted)).toBeDefined();
  expect(decisionMetadata(metadata, undefined)).toEqual(metadata);
});

it.each(["", "   "])(
  "normalizes schema-valid blank controllers only for explicit contracts: %j",
  (mode) => {
    for (const controller of [
      { type: "string" },
      { type: "literal", values: ["enabled", "", "   "] },
    ]) {
      const fields = decisionFields({
        type: "object",
        fields: {
          mode: controller,
          frequency: { type: "union", variants: [{ type: "float" }, { type: "null" }] },
        },
      })!;
      const rules = {
        frequency: { required_when: { field: "mode", value: "enabled" }, empty_otherwise: true },
      };
      const value = { mode, frequency: 6500 };
      const explicit = decisionMetadata({}, { version: 1, fields: rules });
      const normalized = decisionFormValue(fields, value, explicit);
      expect(normalized).toEqual({ mode, frequency: null });
      expect(decisionValueError(fields, normalized)).toBeUndefined();
      expect(decisionConstraintError(normalized, explicit)).toBeUndefined();
      const legacy = decisionMetadata({ field_constraints: rules }, undefined);
      expect(decisionFormValue(fields, value, legacy)).toEqual(value);
      expect(decisionConstraintError(value, legacy)).toBeDefined();
    }
  },
);
