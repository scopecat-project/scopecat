import { expect, it } from "vitest";
import type { InstrumentDescription, InstrumentProperty } from "../../api-contract";
import { hasInstrumentCaptureState } from "./InstrumentInterfaceControls";

const property = (
  capture = true,
  access: InstrumentProperty["access"] = "read_write",
): InstrumentProperty => ({
  id: "value",
  access,
  capture,
  restore: false,
  value_type: { type: "float", finite: true },
});
const description = (capture = true): InstrumentDescription => ({
  instrument_id: "scope",
  implementation_id: "test",
  implementation_version: "1",
  interfaces: [{ id: "test.control/v1", properties: [property(capture)] }],
});
const override = (mount: string) => ({
  property: {
    interface_id: "test.control/v1",
    component_path: [mount, "channel"],
    property_id: "value",
  },
  access: "write_only" as const,
  capture: false,
  restore: false,
});
it("requires a lifecycle capture member, not merely declared state", () => {
  expect(hasInstrumentCaptureState(undefined)).toBe(false);
  expect(hasInstrumentCaptureState({ ...description(), interfaces: [] })).toBe(false);
  expect(hasInstrumentCaptureState(description(false))).toBe(false);
  expect(hasInstrumentCaptureState(description())).toBe(true);
  expect(
    hasInstrumentCaptureState({
      ...description(),
      interfaces: [{ id: "test.control/v1", properties: [property(false, "write_only")] }],
    }),
  ).toBe(false);
});
it("resolves overrides separately for physical mounts and nested interface components", () => {
  const d = description();
  d.interfaces = [
    { id: "test.control/v1", components: [{ id: "channel", properties: [property()] }] },
  ];
  d.interface_mounts = [
    { interface_id: "test.control/v1", component_path: ["a"] },
    { interface_id: "test.control/v1", component_path: ["b"] },
  ];

  d.interface_property_implementations = [override("a")];
  expect(hasInstrumentCaptureState(d)).toBe(true);
  d.interface_property_implementations.push(override("b"));
  expect(hasInstrumentCaptureState(d)).toBe(false);
  d.interface_property_implementations[1] = {
    ...override("b"),
    access: "read_only",
    capture: true,
  };
  expect(hasInstrumentCaptureState(d)).toBe(true);
});
it("honors root implementation overrides in both directions", () => {
  const d = description(false);
  d.interface_property_implementations = [
    {
      property: { interface_id: "test.control/v1", property_id: "value" },
      access: "read_only",
      capture: true,
      restore: false,
    },
  ];
  expect(hasInstrumentCaptureState(d)).toBe(true);
  d.interface_property_implementations[0]!.capture = false;
  expect(hasInstrumentCaptureState(d)).toBe(false);
});
it("includes model-specific capture members independently of interfaces", () => {
  const d = {
    ...description(false),
    device_schemas: [
      {
        id: "model/v1",
        members: [
          { component_path: ["channel"], property: property(false, "write_only") },
          { property: { ...property(false, "read_only"), id: "serial" } },
        ],
      },
    ],
  };
  expect(hasInstrumentCaptureState(d)).toBe(false);
  d.device_schemas[0]!.members[1]!.property.capture = true;
  expect(hasInstrumentCaptureState(d)).toBe(true);
});
