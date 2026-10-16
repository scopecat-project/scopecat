import { expect, it } from "vitest";
import { controlNumber, convertControlNumber } from "./control-quantity";
it.each([
  ["100", "MHz", 100],
  ["1G", "MHz", 1000],
  ["1GHz", "MHz", 1000],
  ["250k", "MHz", 0.25],
  ["1m", "A", 0.001],
  ["1u", "A", 1e-6],
  ["1µA", "mA", 0.001],
  ["1e3kHz", "MHz", 1],
  ["180deg", "rad", Math.PI],
  ["-20dBm", "dBm", -20],
  ["2", null, 2],
])("parses %s in %s", (text, unit, expected) =>
  expect(controlNumber(text, unit)).toBeCloseTo(expected, 12),
);
it.each([
  ["1M", "A"],
  ["1m", "MHz"],
  ["2k", "count"],
  ["2k", "dBm"],
  ["2%", "ratio"],
  ["2G", null],
  ["1wat", "MHz"],
  ["1e", "MHz"],
  ["", "MHz"],
  ["1e309", "Hz"],
  ["1W", "dBm"],
])("rejects %s in %s", (text, unit) =>
  expect(() => controlNumber(text, unit)).toThrow(/finite numbers|Unknown or incompatible unit/),
);
it("converts representation without changing the physical value", () => {
  expect(convertControlNumber("180", "deg", "rad")).toBe(String(Math.PI));
  expect(convertControlNumber("1G", "MHz", "kHz")).toBe("1000000");
});
