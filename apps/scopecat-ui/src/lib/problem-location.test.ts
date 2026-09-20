import { expect, it } from "vitest";
import { problemLocationLabel } from "./problem-location";

it("formats a compile target and its path as a readable location", () => {
  expect(
    problemLocationLabel({
      kind: "model",
      root: "target_compile_entry",
      path: ["acquire", 0, "sample_rate"],
    }),
  ).toBe("target_compile_entry.acquire.0.sample_rate");
  expect(problemLocationLabel({ kind: "model", root: "target_compile_entry", path: [] })).toBe(
    "target_compile_entry",
  );
});
