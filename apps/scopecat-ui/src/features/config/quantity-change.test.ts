import { expect, it } from "vitest";
import { scientificChange } from "./quantity-change";
it("distinguishes equivalent representations and physical changes using generated units", () => {
  expect(scientificChange({ value: 5, unit: "GHz" }, { value: 5000, unit: "MHz" })).toBe(
    "representation",
  );
  expect(scientificChange({ value: 5, unit: "GHz" }, { value: 5100, unit: "MHz" })).toBe(
    "physical",
  );
  expect(scientificChange({ value: 1, unit: "s" }, { value: 1000, unit: "ms" })).toBe(
    "representation",
  );
  expect(scientificChange({ value: 1, unit: "s" }, { value: 1, unit: "Hz" })).toBe("physical");
  expect(scientificChange({ value: 0, unit: "dBm" }, { value: 1, unit: "W" })).toBe("physical");
});
