import { expect, it } from "vitest";
import { calibrationReasonLabel, calibrationStatusLabel } from "./calibration-labels";

it("translates known verdicts and dependency reasons without rewriting unknown values", () => {
  expect(calibrationStatusLabel("usable")).toBe("证据可用");
  expect(calibrationStatusLabel("unknown")).toBe("未知");
  expect(calibrationReasonLabel("dependency_inputs_changed")).toBe("依赖输入已变化。");
  expect(calibrationReasonLabel("check_expired")).toBe("测量已超过所要求的最大有效时长。");
  expect(calibrationStatusLabel("future_state")).toBe("future_state");
  expect(calibrationReasonLabel("author_custom_reason")).toBe("author_custom_reason");
});

it("preserves inherited object keys as unknown raw values", () => {
  for (const value of ["constructor", "toString", "__proto__"]) {
    expect(calibrationStatusLabel(value)).toBe(value);
    expect(calibrationReasonLabel(value)).toBe(value);
  }
});
