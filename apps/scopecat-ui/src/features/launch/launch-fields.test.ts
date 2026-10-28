import { describe, expect, it } from "vitest";
import { canRenderField, quantityInput, quantityDraft } from "./launch-fields";

describe("author scalar form fields", () => {
  it("renders homogeneous scalar choices using the declared field type", () => {
    expect(canRenderField({ type: "integer", enum: [4, 8], default: 8 })).toBe(true);
    expect(canRenderField({ type: "boolean", enum: [true, false] })).toBe(true);
    expect(canRenderField({ type: "string", enum: ["positive", "negative"] })).toBe(true);
    expect(canRenderField({ type: "number", enum: ["1", 2] })).toBe(false);
    expect(canRenderField({ anyOf: [{ type: "string" }, { type: "null" }] })).toBe(false);
  });
});

describe("strict Quantity fields", () => {
  const field = {
    type: "object",
    properties: { value: { type: "number" }, unit: { type: "string" } },
    "x-scopecat-quantity": { dimension: "voltage", unit: "V", minimum: 0, maximum: 3 },
  };
  it("only renders the explicitly marked bounded shape", () => {
    expect(canRenderField(field)).toBe(true);
    expect(canRenderField({ type: "object", properties: field.properties })).toBe(false);
    expect(
      canRenderField({ ...field, properties: { ...field.properties, other: { type: "string" } } }),
    ).toBe(false);
  });
  it("retains explicit units and leaves converted bounds to preview", () => {
    expect(quantityInput('{"value":"2000","unit":"mV"}', field)).toEqual({
      value: 2000,
      unit: "mV",
    });
    for (const raw of [
      "2",
      '{"value":2}',
      '{"value":"","unit":"V"}',
      '{"value":"NaN","unit":"V"}',
      '{"value":2,"unit":"Hz"}',
    ])
      expect(() => quantityInput(raw, field)).toThrow(/有限数值|显式单位|声明范围/);
    const time = { ...field, "x-scopecat-quantity": { unit: "ns", minimum: 1000, maximum: 2000 } };
    for (const value of [1, 2, 0.9999999999999999, 2.0000000000000004])
      expect(quantityInput(JSON.stringify({ value, unit: "us" }), time)).toEqual({
        value,
        unit: "us",
      });
    expect(quantityDraft('{"value":"2e","unit":"mV"}')).toEqual({ value: "2e", unit: "mV" });
  });
});
