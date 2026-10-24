import { describe, expect, it } from "vitest";
import { classes } from "./styles";

describe("classes", () => {
  it.each([
    ["size-9", "size-[30px]"],
    ["w-[min(520px,100%)]", "w-[min(680px,100%)]"],
    ["min-h-9", "min-h-[31px]"],
  ])("replaces conflicting %s with %s while preserving unrelated utilities", (base, override) => {
    expect(classes("grid", base, override)).toBe(`grid ${override}`);
  });

  it("ignores falsey conditional classes", () => {
    expect(classes("grid", false, null, undefined, "gap-2")).toBe("grid gap-2");
  });
});
