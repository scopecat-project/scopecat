import { describe, expect, it } from "vitest";
import { canRenderField } from "./launch-fields";

describe("author scalar form fields", () => {
  it("renders homogeneous scalar choices using the declared field type", () => {
    expect(canRenderField({ type: "integer", enum: [4, 8], default: 8 })).toBe(true);
    expect(canRenderField({ type: "boolean", enum: [true, false] })).toBe(true);
    expect(canRenderField({ type: "string", enum: ["positive", "negative"] })).toBe(true);
    expect(canRenderField({ type: "number", enum: ["1", 2] })).toBe(false);
    expect(canRenderField({ anyOf: [{ type: "string" }, { type: "null" }] })).toBe(false);
  });
});
