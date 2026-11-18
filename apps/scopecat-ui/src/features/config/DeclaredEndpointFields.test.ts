import { expect, it } from "vitest";
import { declaredEndpoints, endpointProblem } from "./DeclaredEndpointFields";
import type { InstrumentDescription } from "../../api-contract";

const description: InstrumentDescription = {
  instrument_id: "source",
  implementation_id: "source",
  implementation_version: "1",
  interfaces: [
    { id: "lab.root/v1", components: [{ id: "sense", components: [{ id: "fine" }] }] },
    { id: "lab.mounted/v2", components: [{ id: "sense", components: [{ id: "fine" }] }] },
  ],
  interface_mounts: [
    { interface_id: "lab.mounted/v2", component_path: ["channels", "ch1"] },
    { interface_id: "lab.mounted/v2", component_path: ["channels", "ch2"] },
  ],
};
it("enumerates root interfaces and nested components below every declared mount", () => {
  const endpoints = declaredEndpoints(description);
  expect(endpoints).toHaveLength(9);
  expect(endpoints).toContainEqual({ interface_id: "lab.root/v1", component_path: [] });
  expect(endpoints).toContainEqual({
    interface_id: "lab.root/v1",
    component_path: ["sense", "fine"],
  });
  expect(endpoints).toContainEqual({
    interface_id: "lab.mounted/v2",
    component_path: ["channels", "ch2", "sense", "fine"],
  });
  for (const endpoint of endpoints)
    expect(
      endpointProblem({ description }, endpoint.interface_id, endpoint.component_path),
    ).toBeUndefined();
  expect(endpointProblem({ description }, "lab.mounted/v2", [])).toContain("组件路径不存在");
  expect(endpointProblem({ description }, "lab.root/v1", ["channels", "ch2"])).toContain(
    "组件路径不存在",
  );
  expect(endpointProblem({ description }, "lab.root/v99", [])).toContain("能力版本不匹配");
});
it("does not invent endpoints for unknown declarations", () => {
  expect(declaredEndpoints(undefined)).toEqual([]);
  expect(endpointProblem(undefined, "lab.root/v1", [])).toContain("声明未知");
});
it("keeps virtual RF source root capabilities selectable when interface_mounts is empty", () => {
  // VirtualRfSource.describe() advertises these interfaces without mounts.
  const rootDescription: InstrumentDescription = {
    instrument_id: "rf",
    implementation_id: "virtual.rf_source",
    implementation_version: "1",
    interfaces: [{ id: "scopecat.rf_output/v2" }, { id: "scopecat.reference_clock/v1" }],
    interface_mounts: [],
  };
  expect(declaredEndpoints(rootDescription)).toEqual([
    { interface_id: "scopecat.rf_output/v2", component_path: [] },
    { interface_id: "scopecat.reference_clock/v1", component_path: [] },
  ]);
  expect(
    endpointProblem({ description: rootDescription }, "scopecat.rf_output/v2", []),
  ).toBeUndefined();
});
